"""Two-run NORMAL-bake tolerance gate for Duskmaw (duskmaw_run.ps1 -Determinism calls it; vampito_bake_diff.py's rule).

The Cycles normal bake is not byte-deterministic (a per-process ray tie-break flips isolated texels; the vampito lane
measured it first). Duskmaw v2's first two full runs (2026-09-26) differed ONLY in the normal bake: geometry/colour, UV,
AO and rig digests were identical, and a single-thread bake did not change that. So the two-run proof is: every other
digest identical + the normal buffers within the limits below.

Limits are pinned from measurement (see LIMITS_NOTE); a failure is a real regression, not the known tie-break.

Usage: python -P duskmaw_bake_diff.py <main.npy> <twin.npy> [<twin.npy> ...]   (exit 0 pass / 1 fail)
"""
import sys

import numpy as np

TEXELS_MAX = 20       # measured worst pair: 5 of 4,194,304 texels (x4 margin)
DELTA_MAX = 0.016     # measured worst pair: 0.00784 = two 8-bit steps (x2 margin)
LIMITS_NOTE = ("measured 2026-09-26, main vs 3 twin builds (log_duskmaw_run.txt BAKE_DIFF): 3 / 2 / 5 texels differ, "
               "max delta 0.00392 / 0.00392 / 0.00784; every other digest identical in all four builds")

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]   # also runs under blender --python
a = np.load(args[0])
ok_all = True
for p in args[1:]:
    b = np.load(p)
    d = np.abs(a - b).max(1)
    texels = int((d > 0).sum())
    mx = float(d.max())
    ok = texels <= TEXELS_MAX and mx <= DELTA_MAX
    ok_all &= ok
    print("BAKE_DIFF %s texels=%d of %d max_delta=%.5f p99_of_differing=%.5f limits=(%d, %.3f) pass=%s" % (
        p, texels, d.size, mx, float(np.percentile(d[d > 0], 99)) if texels else 0.0, TEXELS_MAX, DELTA_MAX, ok))
sys.stdout.flush()
__import__("os")._exit(0 if ok_all else 1)
