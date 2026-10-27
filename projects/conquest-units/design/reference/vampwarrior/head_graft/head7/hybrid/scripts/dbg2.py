import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
Z = np.load(sys.argv[sys.argv.index("--")+1]); V, F, flab = Z["V"], Z["F"], Z["flab"]
te = U.bvh(V, F[flab > 0])
M = np.load(sys.argv[sys.argv.index("--")+2])
H = M["eye0"]; P = H.reshape(-1, 3)
d = np.array([te.find_nearest(__import__("mathutils").Vector(p))[3] if np.isfinite(p[0]) else np.nan for p in P])
np.save(sys.argv[sys.argv.index("--")+3], d.reshape(H.shape[:2]))
