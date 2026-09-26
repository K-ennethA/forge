"""Two-run bake tolerance gate for the Vampire Warrior (vampwarrior_run.ps1 calls this every build; it decides only when
the build and its --digest-only twin differ in nothing but bake_normal / bake_ao).

Limits are vampito's pinned measurement (vampito_bake_diff.py, 2026-09-25: 13 probe runs, worst differing pair 2 of
1,048,576 normal texels, max per-channel delta 0.0039 = one 8-bit step; limits = x8 texels, x2 delta), applied to BOTH bakes
because this unit bakes the same way (Cycles CPU, selected-to-active, seed 0). The texel limit scales with the texture
area (this unit's normal map is 2048^2 = 4x vampito's 1024^2): 16 -> 64 texels for the normal map; the AO map is 1024^2
and keeps 16. The unit's own twin measurement is printed every run and quoted in the lane report; a failure here is a
REAL regression.

Usage: python -P vampwarrior_bake_diff.py <normal_main.npy> <normal_twin.npy> <ao_main.npy> <ao_twin.npy>  (exit 0 / 1)
"""
import sys

import numpy as np

DELTA_MAX = 0.008     # vampito measured worst pair: 0.0039 (one 8-bit step)
TEXELS_PER_MTEXEL = 16  # vampito: 16 texels allowed per 1024^2 texels

ok_all = True
for tag, a_p, b_p in (("normal", sys.argv[1], sys.argv[2]), ("ao", sys.argv[3], sys.argv[4])):
    a = np.load(a_p)
    b = np.load(b_p)
    d = np.abs(a - b).max(1)
    lim = int(round(TEXELS_PER_MTEXEL * len(d) / 1048576.0))
    texels = int((d > 0).sum())
    mx = float(d.max())
    ok = texels <= lim and mx <= DELTA_MAX
    ok_all &= ok
    print("BAKE_DIFF %s texels=%d of %d max_delta=%.5f limits=(%d, %.3f) pass=%s" % (tag, texels, len(d), mx, lim, DELTA_MAX, ok))
sys.exit(0 if ok_all else 1)
