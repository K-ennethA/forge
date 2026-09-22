# Seating (verify numerically, never trial-render)

## WHEN

A part has to seat onto another part's socket or bore — a peg into a bore, an
insert into a seat — and the placement has to be precise before it is worth
looking at, not found by rendering and eyeballing whether it looks flush.

## THE RECIPE

1. **Never trust the placement formula alone.** Measure the actual mesh
   before applying a seat.
2. Use `seat_part` (or the same method by hand): find the bore by **flooding
   across the mesh from a hint point**, stopping at any edge sharper than
   **35°** (`MAX_DIHEDRAL_DEG`), keeping only faces whose normals point **at**
   the axis. A lug's outer wall faces the other way and is excluded by that
   sign test, not by a search radius that happened to be tight enough.
3. Fit the axis by **circle-fitting each depth ring** of the wall (16 slice
   planes across the flooded wall) and then **line-fitting the ring
   centres** — never by "every vertex within N mm of a predicted point,"
   which mixes in geometry that is near the point but not on the bore wall
   (an outer rib, a neighbouring feature).
4. Report **both** radii: circumscribed (where the polygon's vertices sit)
   and inscribed (where its walls sit — what a peg actually has to pass
   through). The gap between them is real clearance budget on a coarse bore,
   not noise.
5. **Verify numerically before applying, or refuse:** tip offset from the
   measured axis, insertion depth against the measured floor, axis-to-axis
   angle against a tolerance derived from the fit's own clearance, and a
   sampled intersection test of the part's body against the base everywhere
   except the bore. A seat that fails any of these is raised as an error
   carrying every one of those numbers, and the scene is left untouched.
6. When a part that is already seated gets moved, propagate the transform to
   its boss ledger's retained cutter/added objects too — otherwise the part
   silently loses the reversibility `attach_boss` gave it.

## WHY

`organic-rework-log.md`, "Ear-R seating — measured, not just formula": the
placement formula said the peg tip would land **0.33 mm** from the socket
axis. Measuring the real mesh (every vertex within 9 mm of the formula's
predicted socket position, 153 vertices captured) gave a socket centre and,
from the peg's own object matrix, a horizontal offset of **0.886 mm** —
*"nearly 3x the formula's optimistic 0.33 mm."* Even after applying the
measured correction, *"the render still shows ear-R short of fully flush...
0.886 mm is small at this object's ~10 mm peg scale but is evidently still
enough to read as a visible seam at this render's zoom."*

`seating.py`'s own worked number for why both radii are reported: a
32-segment bore of nominal radius 3.2 mm has vertices on the 3.2 mm circle
and walls at 3.2·cos(π/32) = **3.1846 mm** — a 0.0154 mm gap the module calls
*"the whole tolerance budget on a coarse 8-segment bore — which is exactly
the class of 'formula said, geometry did' gap that cost eevee-bowl-v2 its
rounds."*

## REJECTED

- **A fixed capture radius around a predicted point**, as the way to find
  the bore. The log's own 9 mm-radius capture mixed in *"the OUTER lug rib
  (not the bore wall)... so it is not a clean axis reading, only a bound."*
  `seating.py`'s docstring names this exact failure mode directly: a hint
  merely inside the search radius "finds whatever cylinder happens to be
  inside the search radius and reports it with total confidence — which is
  the eevee capture-radius failure wearing a better fit."
- **Rendering to see if a seat looks flush**, as the verification step. This
  was the actual method on eevee-bowl-v2 — *"the way each round ended was a
  render, looked at, and disagreed with"* — and it is exactly what
  `seat_part`'s mandatory numeric pre-checks replace.
- **Trusting a boss ledger's recorded world matrix unconditionally.** A part
  moved after its peg was attached leaves a stale record; `seat_part` checks
  the ledger's recorded tip against the actual mesh surface before trusting
  it, and falls back to measuring the peg fresh when it doesn't match.

## SOURCE

- `addon/forge/tools/seating.py` — module docstring (the three lessons/three
  mechanisms section, the circumscribed/inscribed derivation), the
  `MAX_DIHEDRAL_DEG` / `WALL_PERPENDICULAR_MAX` / `RING_SLICES` constants and
  their derivations.
- `projects/eevee-bowl-v2/design/organic-rework-log.md` — section "2. Ear-R
  seating — measured, not just formula."
