"""vampwarrior_rawhead_islands.py -- <raw.npz> <out.npy> : UV island id per raw face (faces joined across an edge only when
both corner UVs match on that edge)."""
import sys, numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
Z = np.load(sys.argv[1]); F = Z["F"]; U = np.round(Z["FUV"] * 65536).astype(np.int64)
rows = []
for a, b in ((0, 1), (1, 2), (2, 0)):
    va, vb = F[:, a], F[:, b]; ua, ub = U[:, a], U[:, b]
    sw = va > vb
    k0 = np.where(sw, vb, va); k1 = np.where(sw, va, vb)
    u0 = np.where(sw[:, None], ub, ua); u1 = np.where(sw[:, None], ua, ub)
    rows.append(np.c_[k0, k1, u0, u1, np.arange(len(F))])
R = np.vstack(rows)
o = np.lexsort(R[:, :6].T[::-1]); R = R[o]
same = (R[1:, :6] == R[:-1, :6]).all(1)
i = R[:-1, 6][same]; j = R[1:, 6][same]
A = coo_matrix((np.ones(len(i)), (i, j)), shape=(len(F), len(F)))
n, lab = connected_components(A, directed=False)
np.save(sys.argv[2], lab.astype(np.int32))
cnt = np.bincount(lab)
print("ISLANDS", n, "faces/island p50", int(np.median(cnt)), "max", int(cnt.max()), "singletons", int((cnt == 1).sum()))
