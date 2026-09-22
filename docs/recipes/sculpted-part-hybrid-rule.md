# Sculpted-part hybrid rule (function plus character)

## WHEN

A design needs both mechanical function (holds something, stands up, fits a
socket) and organic character (a creature's face, fur, ears, a face on a
device) — "make me a real X, but make it look like Y."

## THE RECIPE

1. **Split the design**: a parametric base with sockets does the functional
   work (holds the payload, stands up, does not tip). Organic/character
   pieces are separate parts that plug into the base by peg. Never sculpt
   the functional geometry and never parametrize the character geometry —
   the split is by *what the piece is for*, not by which is easier.
2. **One `peg_spec` is the single source of truth** for both halves: the
   base's sockets (`socket_for(spec, tol)`, opening upward) and every
   appendage's own peg are built from the same spec.
3. **The base's body is a `soft_body` profile** (a handful of `(radius, z)`
   points), not a plain cylinder, wherever the reference silhouette swells
   or tapers. A straight cylinder under an organic collar reads as a hat
   brim; the same collar over a swelling/tapering profile reads as a
   shoulder.
4. **Decoration is parametric, never sculpted** — `leaf_collar`,
   `petal_crown`, `scale_band`, `textured_band`. And never a flat cut-out:
   pass `cup=` and `layers=2` so a flat element reads as a stamped slab while
   a cupped, layered one reads as fur or feather depth.
5. **Each piece gets its own check.** On eevee-bowl-v2: parametric core
   (Build123d/`forge_lib`) built the base body (bowl seat, ear-slot lugs,
   tail dovetail boss); organic pieces (`generate_3d` off the sheet's own
   drawings, via `silhouette_part`/`leaf_collar`) built the two ears, the
   tail, and the fur collar — four separate `/check` runs, not one shared
   one.
6. Bores, seats, sockets and pockets are cut into the parametric body
   **after** the swept profile exists as a solid — there is nothing special
   about a revolved or `soft_body` solid once it is one; ordinary boolean
   cuts apply.

## WHY

`docs/part-authoring.md`, Pattern C, on the failure this pattern replaces:
*"Given a reference image of a bowl holder with a fur collar and ears, a
generator that only knows cylinders and grooves produces a correct bowl
ring, a **dashed groove** where the fur should be, and **flat slabs** for the
ears. The base was never the problem. The decoration was — and the fix is
not to sculpt it, it is to notice that the decoration is parametric too."*

And on passing checks not being the brief: *"Then the ornament helpers
landed, every check passed, and the result still read as **stiff, sparse and
flat**: flat leaves are cut-outs, and a cylinder is not a body. Passing four
checks is necessary and it is not the brief."* The fix was two more
parameters (`cup=` on the collar, `soft_body` under it) plus a measurement a
screenshot would not give: rays cast from the band's axis through the gaps
between one rank's leaves, to prove the second rank actually covers them.

eevee-bowl-v2's own `requirements.md`, Build plan: *"Parametric core
(Build123d, via the geometry service, `forge_lib` helpers): base body (bowl
seat, ear-slot lugs, tail dovetail boss)... Organic pieces (generated meshes
off the sheet's own drawings... `leaf_collar` for the fur, `silhouette_part`
for the ears and tail...): two ears, the tail, the fur collar. Each piece
gets its own `/check`... before export."*

## REJECTED

- **Sculpting the decoration by hand once the base checks passed.** The
  documented fix was the opposite: *"the fix is not to sculpt it, it is to
  notice that the decoration is parametric too."*
- **Treating a passing check as "done."** A cylinder body under organic
  decoration passed every check and still read wrong; the fix needed a
  fourth axis (a cast-ray coverage measurement) that no printability check
  covers.
- **A plain cylinder body under organic decoration** — reads as a hat brim
  against a straight wall; replaced with a `soft_body` swept profile.

## SOURCE

- `docs/part-authoring.md` — section "C. Hybrid designs: function plus
  character".
- `projects/eevee-bowl-v2/design/requirements.md` — "Build plan" section
  (the actual parametric/organic split used on this project).
- `addon/forge/tools/bosses.py` — the peg/socket reversibility this split
  depends on for iterating the organic pieces' placement.
