import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); V, F, flab = Z["V"], Z["F"], Z["flab"]
t = U.bvh(V, F)
ECS = [V[np.unique(F[flab == k])].mean(0) for k in (1, 2)]
out = {}
for i, c in enumerate(ECS):
    H, FI, xs, zs = U.ortho_front(t, c[0] - 0.09, c[0] + 0.09, c[2] - 0.05, c[2] + 0.05, 0.0005)
    out["H%d" % i] = H; out["FI%d" % i] = FI
np.savez_compressed(a[1], **out)
