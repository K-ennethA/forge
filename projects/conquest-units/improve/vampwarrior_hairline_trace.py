"""Vampwarrior v4.2: trace the artist's hairline annotation into head_front pixels (plain Python: forge service venv).

    python -P vampwarrior_hairline_trace.py [--cap-trace <json>]

design/reference/vampwarrior-v42-hairline-annotation.png is a 388 x 321 screenshot of the v4.1 head_front still with an
ORANGE line (the front strand's top edge) and a GREEN line (the scalp hair's outline) drawn on it. Steps:
  1. the line pixels (colour masks; the largest connected component of each, so the red eyes never count as orange)
  2. registration onto renders/vampwarrior/vampwarrior_v41_head_front.png: masked SSD over scale (0.540 .. 0.566, step
     0.001) x translation (FFT), line pixels + 3 px excluded, parabolic sub-pixel peak
  3. every line pixel mapped into head_front pixels; each line binned by angle (2 deg) about the head point C, the two
     halves folded about the part and their radii averaged (the model is symmetric; the freehand halves are not), then
     mirrored back -> symmetric polylines
Writes renders/vampwarrior/vampwarrior_v42_hairline_trace.json (pixel lines + registration). --cap-trace merges the
one-time cap-ray trace (the lines' idle:1 camera rays on the v4.1 hair cap -> rest (psi, el)) that CURTAIN_OUTER /
HAIR_BAND_VOL were read off; the v4.1 rigged file it ran on is replaced by the v4.2 build, so it is kept as a record.
"""
import json
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage, signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ANN = os.path.join(ROOT, "design", "reference", "vampwarrior-v42-hairline-annotation.png")
BASE = os.path.join(ROOT, "renders", "vampwarrior", "vampwarrior_v41_head_front.png")
OUT = os.path.join(ROOT, "renders", "vampwarrior", "vampwarrior_v42_hairline_trace.json")
C = (516.5, 345.0)          # head_front pixel inside both arcs: on the part line (eye midpoint x 516.7), eye height

ann = np.array(Image.open(ANN).convert("RGB")).astype(float)
r, g, b = ann[..., 0], ann[..., 1], ann[..., 2]
masks = {"orange": (r > 180) & (g > 60) & (g < 170) & (b < 80),
         "green": (g > 100) & (r < 90) & (b < 130) & (g - r > 40)}
for k, m in masks.items():
    lab, n = ndimage.label(ndimage.binary_dilation(m, iterations=1))
    big = 1 + int(np.argmax(ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))))
    masks[k] = m & (lab == big)
M = (~ndimage.binary_dilation(masks["orange"] | masks["green"], iterations=3)).astype(float)
A = ann.mean(2)
T = A * M
base = Image.open(BASE).convert("RGB")
best = None
for s in np.arange(0.540, 0.566, 0.001):
    im = np.array(base.resize((round(1024 * s), round(1024 * s)), Image.BICUBIC)).astype(float).mean(2)
    ssd = (signal.fftconvolve(im ** 2, M[::-1, ::-1], mode="valid") - 2 * signal.fftconvolve(im, T[::-1, ::-1], mode="valid")
           + (T ** 2).sum()) / M.sum()
    k = np.unravel_index(np.argmin(ssd), ssd.shape)

    def sub(a, b_, c):
        d = a - 2 * b_ + c
        return 0.0 if d == 0 else 0.5 * (a - c) / d
    oy = k[0] + sub(ssd[k[0] - 1, k[1]], ssd[k], ssd[k[0] + 1, k[1]])
    ox = k[1] + sub(ssd[k[0], k[1] - 1], ssd[k], ssd[k[0], k[1] + 1])
    row = (float(ssd[k]) ** 0.5, round(1024 * s) / 1024.0, float(oy), float(ox))
    if best is None or row[0] < best[0]:
        best = row
rms, S, OY, OX = best
out = {"registration": {"scale": S, "offset_y": OY, "offset_x": OX, "rms_grey": round(rms, 2),
                        "rule": "head_front px = (annotation px + 0.5 + offset) / scale - 0.5"}, "centre_px": C}
for nm, m in masks.items():
    ys, xs = np.nonzero(m)
    X = (xs + 0.5 + OX) / S - 0.5
    Y = (ys + 0.5 + OY) / S - 0.5
    ang = np.degrees(np.arctan2(-(Y - C[1]), X - C[0]))
    rad = np.hypot(X - C[0], Y - C[1])
    fold = np.where(ang > 90, 180 - ang, ang)
    left = ang > 90
    rows = []
    for a0 in np.arange(-10, 90, 2.0):
        kl = (fold >= a0) & (fold < a0 + 2) & ~left
        kr = (fold >= a0) & (fold < a0 + 2) & left
        if kl.any() and kr.any():
            rows.append((a0 + 1.0, float(np.median(rad[kl])), float(np.median(rad[kr]))))
    half = [[C[0] + 0.5 * (rl + rr) * np.cos(np.radians(a)), C[1] - 0.5 * (rl + rr) * np.sin(np.radians(a))]
            for a, rl, rr in rows[::-1]]                      # apex -> her left end (image right)
    line = [[2 * C[0] - p[0], p[1]] for p in half[::-1]] + half
    out[nm] = [[round(p[0], 2), round(p[1], 2)] for p in line]
    out[nm + "_halves_radius_diff_px_max"] = round(max(abs(rl - rr) for _, rl, rr in rows), 1)
    out[nm + "_pixels"] = int(m.sum())
if "--cap-trace" in sys.argv:
    out["v41_cap_trace"] = json.load(open(sys.argv[sys.argv.index("--cap-trace") + 1]))
elif os.path.exists(OUT):
    old = json.load(open(OUT))
    if "v41_cap_trace" in old:
        out["v41_cap_trace"] = old["v41_cap_trace"]
json.dump(out, open(OUT, "w"), indent=1)
print("TRACE", json.dumps(out["registration"]), "orange", len(out["orange"]), "green", len(out["green"]))
