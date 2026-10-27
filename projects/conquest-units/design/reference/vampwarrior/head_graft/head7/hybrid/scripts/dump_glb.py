"""dump_glb.py <in.glb> <out.npz> : head glb -> f-frame (x, -z, y) arrays: welded V, F, per-corner UV (FUV), tex, comp label per face"""
import sys, numpy as np
sys.path.insert(0, r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units\improve")
import proxy_finish as PF
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
V, F, UV, TI, ims = PF.read_proxy(sys.argv[1])
tex = list(ims.values())[0]
Vf = np.stack([V[:, 0], -V[:, 2], V[:, 1]], 1)
q = np.round(Vf / 1e-6).astype(np.int64)
_, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True); inv = inv.ravel()
Vw = Vf[first]; Fw = inv[F]
FUV = UV[F]
n = len(Vw)
r = np.concatenate([Fw[:, 0], Fw[:, 1]]); c = np.concatenate([Fw[:, 1], Fw[:, 2]])
A = coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n))
nc, lab = connected_components(A, directed=False)
flab = lab[Fw[:, 0]]
ids, cnt = np.unique(flab, return_counts=True)
order = ids[np.argsort(-cnt)]
remap = {int(o): i for i, o in enumerate(order)}
flab = np.array([remap[int(x)] for x in flab])
# open / nonmanifold edges
E = np.sort(np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]]), 1)
_, ec = np.unique(E, axis=0, return_counts=True)
print("verts", n, "tris", len(Fw), "comps", nc, [int((flab == i).sum()) for i in range(min(nc, 6))],
      "open", int((ec == 1).sum()), "nonmanifold", int((ec > 2).sum()), "bbox", Vw.min(0).round(4), Vw.max(0).round(4), "tex", tex.shape)
np.savez_compressed(sys.argv[2], V=Vw, F=Fw, FUV=FUV, tex=tex, flab=flab)
