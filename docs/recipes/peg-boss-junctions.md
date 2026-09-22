# Peg / boss junctions

## WHEN

A parametric joint feature (a peg, a boss) needs to be attached onto a
sculpted or organic part by boolean union — especially when the angle or
position is going to need iteration, which it always does on a hand-fitted
joint.

## THE RECIPE

1. **Attach with `attach_boss`, not a one-shot union.** It keeps both the
   cutter *and* the material the union actually added (`boss − target`,
   computed before the union), which is what makes the attach reversible.
   A raw union has no inverse: subtracting the same peg back out removes the
   buried volume too, not just what it added.
2. **Always give a buried peg a fillet skirt at its root**: a truncated cone,
   wide at the buried end, tapering to the peg's own radius at the point the
   bare shaft begins, pre-shifted so its narrow end sits at local Z=0 so it
   takes the exact same Pos/Rot as the peg and blends with no seam
   (`projects/eevee-bowl-v2-peg-skirt/part.py` defaults: `peg_r=3.0 mm`,
   `flare=3.0 mm`, `height=3.0 mm`).
3. **When the angle is wrong, use `move_boss`** to detach and re-union the
   *same retained cutter* at a new transform. Do not union a second peg next
   to the wrong one, and do not regenerate the whole part — both were tried
   on this project and cost a full rebuild or a worse wall.
4. **Detach (`detach_boss`) subtracts the retained "added" solid**, not the
   peg itself, and only after inflating it along its own vertex normals by
   `SEAM_EPSILON_FRACTION = 0.02 ×` the target's own median edge length
   (clamped to `[0.002, 0.5]` mm). Never subtract at zero inflation.
5. **Re-derive the attach angle and seating rotation per part** — do not
   reuse a prior part's numbers. On eevee-bowl-v2's ear, the corrected peg
   build angle measured out to **145.7°**, with a seating rotation of
   **[34.3°, 0°, rim_angle°]** (the 34.3° being the X-component) — both are
   that ear's own measured corrections, not constants.

## WHY

**Why the naive inverse is wrong** (`bosses.py` module docstring, measured on
its own fixture): subtracting a 6×11 mm peg whole, seated 70° into a 25 mm
sculpt, leaves a **7.0 mm bore** where the part used to be solid — wrong by
millimetres, not rounding.

**The seam-epsilon sweep** (`bosses.py`, measured on a 48×24 UV sphere,
median edge 3.247 mm):

| fraction | epsilon | surface departure from pre-attach | volume error |
|---|---|---|---|
| 0.0 | 0.0 mm | 4.937 mm (sliver — unusable) | +0.00026 % |
| 0.005 | 0.016 mm | 0.096 mm | −0.00020 % |
| **0.02** | 0.065 mm | **0.124 mm** | −0.00196 % |
| 0.10 | 0.325 mm | 0.325 mm | −0.01302 % |

0.02 is "the largest fraction that is still under" the floor where departure
stops improving (≈0.1 mm, which is the fixture's own ±0.6 mm facet noise
being re-cut at the seam, not the epsilon). After a full attach/detach cycle,
volume returns to within **0.002 %** of pre-attach (−1.27 mm³ of 64 788 mm³)
and the surface to within **0.124 mm** Hausdorff.

## REJECTED

- **"Subtract the peg again"** as the inverse — wrong by 7.0 mm (measured
  above), because most of a seated peg is buried target material, not added
  material.
- **Unioning a corrected peg alongside the wrong one** — "two pegs, worse
  walls" (`bosses.py`), the actual first-attempt failure on this project.
- **Zero seam epsilon on detach** — a 4.937 mm sliver, "unusable" by the
  module's own measurement.
- **Building the peg + rib + skirt as one bmesh** instead of separate objects
  unioned in sequence — measured on Blender 5.0.1: an operand made of three
  overlapping shells **deletes the target outright** ("the sculpt came back
  with zero vertices"). Each primitive piece must be its own object, unioned
  one at a time.
- **The `FAST` boolean solver** instead of `EXACT` — `bosses.py`: *"Never
  FAST: it is a float-tolerance solver, and the whole contract here is that
  the added solid is the exact complement of the union."*
- **Leaving `use_self` off** the boolean modifier — an operand made of
  overlapping shells silently annihilates the target without it (same
  zero-vertices failure as above).

## SOURCE

- `addon/forge/tools/bosses.py` — module docstring (the eevee-bowl-v2 origin
  story, the naive-inverse math, the seam-epsilon table and its derivation),
  `_piece_object` and `_boolean` inline comments (the overlapping-shells and
  `use_self` failures).
- `projects/eevee-bowl-v2-peg-skirt/part.py` — the fillet-skirt geometry and
  its default dimensions.
- `projects/eevee-bowl-v2/design/organic-rework-log.md` — the 145.7° peg
  angle and `[34.3, 0, rim_angle]` seating rotation.
- `addon/tests/headless_bosses.py` — fixture description ("3 mm fillet
  skirt"), and the `EXACT`/`use_self` behavior asserted under test.
