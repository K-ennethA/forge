"""vampwarrior_rawhead_zone.py -- <raw.npz> <raw_tex.png> <fixed_tex.png> <texel_pos.npy> <eyecontour.npz> <out.npy> :
per-RAW-vertex smoothing weight for the eye-smear zones. Where the texture fix painted the grey smears out, the raw shell's
small flaps / pinch dimples along the old smear border become visible (the smear used to hide them). Weight 1 within
0.002 of a painted-out eye-zone texel (3D, via the texel position map), fading to 0 at 0.006; forced to 0 within 0.003 of
the eye-opening contour (fading back by 0.006), so the opening and the lash line are untouched; and only below the
openings (weight fades out between z 0.086 and 0.090), so the liner wings are untouched."""
import sys, numpy as np
from PIL import Image
from scipy.spatial import cKDTree
Z = np.load(sys.argv[1]); V = Z["V"]
A = np.asarray(Image.open(sys.argv[2]).convert("RGB")).astype(int); B = np.asarray(Image.open(sys.argv[3]).convert("RGB")).astype(int)
P = np.load(sys.argv[4]); EC = np.load(sys.argv[5])
ch = (np.abs(A - B).max(2) > 3) & np.isfinite(P[..., 0])
Q = P[ch]; Q = Q[(Q[:, 2] < 0.125) & (Q[:, 1] < -0.08)]
d, _ = cKDTree(Q[::3]).query(V, distance_upper_bound=0.05)
w = np.clip((0.006 - d) / 0.004, 0, 1)
C = np.vstack([EC["eye0"], EC["eye1"]])
dc, _ = cKDTree(C).query(V)
w *= np.clip((dc - 0.003) / 0.003, 0, 1)
# only BELOW the openings (the dimples sit on the old lower smear border); liner wings / upper lid untouched
zlo = 0.088   # the liner wings start above z ~0.095 (both eyes); the lower lids / dimples sit at z 0.065-0.085
w *= np.clip((zlo + 0.002 - V[:, 2]) / 0.004, 0, 1)
np.save(sys.argv[6], w.astype(np.float32))
print("ZONE verts w>0", int((w > 0).sum()), "w=1", int((w >= 0.999).sum()), "zone texels", len(Q))
