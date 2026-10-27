import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); out = a[1]
t = U.bvh(Z["V"], Z["F"])
H, FI, xs, zs = U.ortho_front(t, -0.2, 0.2, -0.3, 0.2, 0.001)
np.savez(out + "_map.npz", H=H, FI=FI, xs=xs, zs=zs)
print("done", H.shape)
