import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
Z = np.load(sys.argv[sys.argv.index("--")+1]); V, F, flab = Z["V"], Z["F"], Z["flab"]
t = U.bvh(V, F)
H, FI, xs, zs = U.ortho_front(t, 0.03, 0.21, 0.044, 0.144, 0.002)
print("hits", (FI>=0).sum(), "eyeball", np.isin(FI[FI>=0], np.nonzero(flab>0)[0]).sum())
print("flab counts", np.bincount(flab))
