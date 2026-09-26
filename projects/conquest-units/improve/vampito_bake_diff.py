"""Two-run NORMAL-bake tolerance gate (vampito_run.ps1 calls this when only bake_normal digests differ).

The normal bake is not byte-deterministic on this mesh: a per-process Cycles tie-break on the thin wing
membrane flips a couple of texels by one 8-bit quantization step. Measured 2026-09-25 over 13 probe runs
(digest-only builds of vampito_build.py at both cage 0.06 and 0.12, multi- and single-threaded): worst
differing pair = 2 of 1,048,576 texels, max per-channel delta 0.0039 (exactly one 8-bit step); AO and all
geometry/uv/weights/keys digests were identical in every run. Limits below = measured worst case with
margin (x8 texels, x2 delta). A failure here is a REAL regression, not the known tie-break.

Usage: python -P vampito_bake_diff.py <main.npy> <twin.npy>   (exit 0 pass / 1 fail)
"""
import sys

import numpy as np

TEXELS_MAX = 16       # measured worst pair: 2
DELTA_MAX = 0.008     # measured worst pair: 0.0039 (one 8-bit step)

a = np.load(sys.argv[1])
b = np.load(sys.argv[2])
d = np.abs(a - b).max(1)
texels = int((d > 0).sum())
mx = float(d.max())
ok = texels <= TEXELS_MAX and mx <= DELTA_MAX
print("BAKE_DIFF texels=%d max_delta=%.5f limits=(%d, %.3f) pass=%s" % (texels, mx, TEXELS_MAX, DELTA_MAX, ok))
sys.exit(0 if ok else 1)
