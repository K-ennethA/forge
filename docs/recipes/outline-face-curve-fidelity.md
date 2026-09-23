# A traced outline's face must use the SAME interpolation as the script's own math

## WHEN

A script derives a face from a list of traced/measured `(u, v)` points AND
separately reasons about widths, spans or bands at points *between* those
knots (a cup inset, a fillet-radius clamp, a "this band is >= N mm wide"
comment) — and the face itself is built with a smooth curve
(`Spline(..., periodic=True)`) through the same points.

## THE RECIPE

1. Check what interpolation the script's own math assumes. Linear
   interpolation between knots (`_interp_chain` walking pairs and lerping)
   means the reasoning is about a POLYLINE, not a spline.
2. If the face is built from `Spline(*outline, periodic=True)`, the built
   geometry and the reasoning have silently diverged: a spline can overshoot
   near a sharp local direction change (worst on a concave edge, and worst
   right next to a deliberately "blunted" corner — exactly where thin-wall
   fixes tend to live), producing an actual boundary narrower than the
   polyline the math promised.
3. Fix it by making the built geometry equal the model: use
   `Polyline(*outline, close=True)` (straight edges through the same
   points) instead of `Spline`. No other change needed — the band-width
   reasoning becomes true rather than approximate.
4. Re-run the gate's own probes after the swap; don't assume "same points,
   so same shape."

## WHY (measured)

Benchmark ear-sculpt, 2026-09-23 round 3: a script that computed a root
"blunted shelf" specifically sized to keep >= 2 mm of local width before
filleting (comments citing the exact reasoning), built from
`Spline(*outline, periodic=True)`, still failed `min_wall` with 4 probes at
0.145 mm right at the root shelf (v = 0.012 of the length) — inside the band
the comments said was safe. Swapping only that one line to
`Polyline(*outline, close=True)` cleared the FAIL entirely: side=1 thinnest
1.09 mm (clean pass), side=-1 thinnest 0.897 mm (WARN band, still >= the 0.8
mm floor). Mesh complexity dropped too (2326 -> 384 verts), consistent with
the spline's extra curvature no longer being tessellated.

## REJECTED

- Trusting "the face is built from the same points the width math uses" as
  sufficient — it is not, when the curve types differ.
- Chasing the root band with more blunting/offset before checking the curve
  type — would have shrunk the safety margin further without fixing the
  actual mismatch.

## FOLLOW-UP (round 4) — the polyline can be TOO coarse

Swapping to a 12-point `Polyline` fixed `min_wall` but cost shape fidelity:
silhouette IoU (the outline traced from the artist's sheet, scored as
intersection-over-union) dropped to 0.646 against a 0.74 floor — a 12-point
straight-edged polygon reads as an angular obelisk, not a curved ear, and a
rectangular inner-ear cup made it worse. The fix is not "pick one problem to
have": sample the SAME periodic Spline (still fit through the 12 traced
points) into ~100 points, and build the Polyline from those dense samples
instead of the 12 control points. Every width calculation in the script
switches to reading the same dense point list (split into two monotonic-in-v
chains by finding the loop's own v-min/v-max, not by assuming the samples
land at particular control-point indices — chord-length parameterisation
doesn't guarantee that). Result: min_wall thinnest 1.178 mm (clean pass,
both mirrored sides identical), up from round 3's 0.897–1.09 mm, because the
dense sampling also let the inner-ear cup become an inset copy of the real
outline instead of a rectangle, removing another source of local pinching.
The general lesson: when "smooth" and "exactly matches the width math" seem
to be in tension, check whether the model can just be sampled densely enough
that a polyline built from it and the model that reasons about it are
effectively the same curve — a resolution parameter, not a forced choice.

## SOURCE

`projects/bench-ear-sculpt/part.py` round 3 (this session): the
`Spline(*outline, periodic=True)` -> `Polyline(*outline, close=True)` edit
and the before/after `partforge_check` reports quoted above. Round 4 (this
session): `_dense_outline`/`_split_dense_chains`, and the min_wall numbers in
the follow-up section above. Sibling:
[constructional-thinness.md](constructional-thinness.md) (this is a distinct
cause of the same symptom — a curve-fidelity mismatch rather than a
too-small blunt radius).
