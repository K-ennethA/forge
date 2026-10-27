"""eyepx.py -- <assembled.npz> <src eyemaps npz> : visible eyeball px in the front view (eye boxes of the source maps)"""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); t = U.bvh(Z["V"], Z["F"]); part = Z["part"]
S = np.load(a[1])
out = []
for i in (0, 1):
    Hs = S["H%d" % i]
    xs = Hs[..., 0]; zs = Hs[..., 2]
    # same box as the source maps: rebuild from the first finite row / col
    c = np.nanmean(Hs.reshape(-1, 3), 0)
    H, FI, X, Zs = U.ortho_front(t, c[0] - 0.09, c[0] + 0.09, c[2] - 0.05, c[2] + 0.05, 0.0005)
    hit_eye = (FI >= 0) & (part[np.maximum(FI, 0)] == 1)
    out.append(int(hit_eye.sum()))
    np.save(a[0].replace(".npz", "_eyepart%d.npy" % i), np.where(FI >= 0, part[np.maximum(FI, 0)], -1))
print("EYEPX", json.dumps(out))
