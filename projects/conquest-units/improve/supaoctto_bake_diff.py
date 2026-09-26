"""Two-run BAKE tolerance gate (supaoctto_run.ps1 calls this when the only digests that differ are bake_normal / bake_ao).

The vampito and duskmaw normal bakes are not byte-deterministic (a per-process Cycles tie-break flips a few texels by one
or two 8-bit steps). This compares the main build's float buffers with the twin's. Limits: see LIMITS_NOTE (pinned
from this unit's own measurement; a failure is a REAL regression, not the known tie-break).

Usage: python -P supaoctto_bake_diff.py <main_normal.npy> <twin_normal.npy> <main_ao.npy> <twin_ao.npy>  (exit 0 pass / 1 fail)
"""
import sys

import numpy as np

TEXELS_MAX = 16       # measured worst pair: 1 of 1,048,576 normal texels (x16 margin; kept equal to vampito's pin)
DELTA_MAX = 0.008     # measured worst pair: 0.00392 = one 8-bit step (x2 margin)
LIMITS_NOTE = ("measured 2026-09-26, two main-vs-twin runs of supaoctto_run.ps1: run 1 byte-identical (bake included); "
               "run 2 normal 1 texel differs by 0.00392, AO identical; every geometry/uv/weights/keys digest identical both runs")

ok = True
for tag, a_, b_ in (("normal", sys.argv[1], sys.argv[2]), ("ao", sys.argv[3], sys.argv[4])):
    a = np.load(a_)
    b = np.load(b_)
    d = np.abs(a - b).max(1)
    texels = int((d > 0).sum())
    mx = float(d.max())
    ok_ = texels <= TEXELS_MAX and mx <= DELTA_MAX
    ok &= ok_
    print("BAKE_DIFF %s texels=%d of %d max_delta=%.5f limits=(%d, %.3f) pass=%s" % (tag, texels, len(d), mx, TEXELS_MAX, DELTA_MAX, ok_))
sys.exit(0 if ok else 1)
