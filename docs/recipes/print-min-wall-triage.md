# Print min-wall triage

## WHEN

`partforge_check` (`min_wall`, `overhangs`, `watertight`, `bed_fit`) fails on
a printed part and the fastest correct fix is needed, rather than guessing at
which slider to move.

## THE RECIPE

First, know what the number means: `min_wall` casts a ray **inward along each
facet's own normal** and reports the first hit (`service/checks.py`). It
measures along the normal, not the shortest path (a wedge thin across some
*other* axis reads thicker than it prints), and a narrow gap between two
faces reads as a thin wall — conservative, but the number reported is a gap,
not a wall. Treat a fail as "look here," not as a dimension.

Then triage by failure shape:

| Failure | First move |
|---|---|
| `min_wall` on a sloped or tapered face | Replace the raw revolve/loft with `blunted_taper` or `flared_lip`. This is the common case. |
| `min_wall` on decoration | Replace added ribs with `textured_band`, or pass `wall=` so its clamps apply. |
| `min_wall` on an outline you extruded yourself | Almost always a convex edge under 90° in **plan** view, or a spline outline extruded straight (OCC's mesh of a B-spline side wall pinches inside its own cap). Use `silhouette_part`, which splines the outline and samples it into a polygon before extruding. |
| Decoration that came out as a groove or a slab | It is not sculpture. A collar of overlapping elements is `leaf_collar`/`petal_crown`/`scale_band`; an ear, tail, fin or wing is `silhouette_part`. |
| Decoration that reads flat, stiff or sparse | The elements are cut-outs. Pass `cup=0.3`, leave `layers=2` — flat reads as cut-outs, cupped reads as fur/feathers. Then check the body underneath is a `soft_body`, not a cylinder. |
| `overhangs` on a collar's band rim after the body stopped being a cylinder | The band's bore is a cylinder; size `ring_radius` to the **narrowest** the body gets over `band_height_mm`, not its radius under the band's top. |
| `min_wall` on a pocket floor | Pass `available_depth=` to `magnet_pocket`; derive the part's thickness from the pocket depth, not the other way round. |
| `min_wall` on a shell | `shell_box` / `wall_safe_shell`, let it clamp the wall. |
| `overhangs` on a base | `arcade_base(arch="pointed")` or `feet_ring`. |
| `overhangs` on a cone or hole | Check `role`: a solid flares dangerously upward, a hole dangerously downward. Let `support_free=True` clamp it. |
| `overhangs` with a better orientation offered | Rebuild the part standing that way up — the model should be modelled in its print orientation. |
| `bed_fit`, with a feasible `suggested_segmentation` | **Not a failure to fix.** Hand `suggested_segmentation.mode` to `/segment` at print time. Never shrink `max` to avoid this. |
| `bed_fit`, with `suggested_segmentation.feasible: false` | The real one — too big even segmented. Shrink the declared `max` or rework the shape, and put the choice to the user. |
| `watertight` | Almost always a coplanar-face boolean. Make every cutter overshoot the face it enters by 0.2–1 mm. |

Two more failure shapes worth knowing before they happen:

- **A knife-edge / acute taper** leaves a rim a fraction of a millimetre
  thick at its narrow end even when the nominal wall is fine — measured:
  *"comes back as `min_wall: fail, thinnest 0.076 mm`."* Give the acute end
  a straight vertical land of at least `max(min_land, min_wall)`, or call
  `blunted_taper()` / `flared_lip()`, which do it automatically.
- **A textured groove** must clamp its own depth: `min(depth, 0.40 × wall,
  wall − min_wall, 0.45 × surface_width)`. `textured_band()` applies all
  four; a hand-rolled groove needs all four by hand.
- **Never let a computed thickness land exactly on a limit.** `min_wall`
  compares with a strict `<`, so a floor computed as `thickness -
  pocket_depth` intended to be exactly 1.0 mm can fail on float noise. Bias
  the computed value off the limit on purpose.

## WHY

Printer profile numbers this triage runs against (`docs/part-authoring.md`):
`min_wall_thickness` 0.8 mm is the hard floor, `min_feature_size` 1.0 mm the
soft floor (0.8–1.0 mm is a warn), and both derive from the printer's own
`nozzle_diameter` 0.4 mm — *"two perimeters is 0.8 mm — that is where
`min_wall` comes from."* `max_unsupported_overhang_deg` is 50° from vertical.
The 0.076 mm knife-edge failure and the four-term groove-depth clamp are both
measured/derived in the same document's authoring rules.

## REJECTED

This table *is* the rejected-approaches list: each "first move" replaced a
raw, hand-rolled geometry approach (a bare revolve/loft, a hand-cut groove, a
flat stamped leaf) that produced exactly the failure in its row. None of the
helpers named here were the first thing tried — they are what replaced the
approach that failed the check.

## SOURCE

- `docs/part-authoring.md` — "### What the checks actually measure", "## 4.
  The workflow law" (the failure/first-move table), and the knife-edge and
  groove-depth-clamp notes earlier in the same document.
