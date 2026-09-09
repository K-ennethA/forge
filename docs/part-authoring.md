# Writing a PartForge part script

Audience: an LLM writing a `part.py` on request ("make me a small magnet
holder"). This is the rulebook. Read it before writing geometry, not after the
checks fail.

The one-sentence version: **compose the part out of `forge_lib` helpers, then
run `/check`, and never ship a script whose checks you have not seen pass.**

Reference implementations to imitate:

| File | Shape of part |
|---|---|
| `service/samples/magnet_holder.py` | A flat bar: pockets, rounded ends, countersunk holes. Passes all four checks at defaults and at both ends of every range. |
| `projects/bowl-holder/part_base_ring.py` | A ring: revolved profile, flared foot, fluted band, arcaded base, appendage sockets. |
| `service/samples/appendage_peg.py` | Two parts from one script, joined by a keyed peg. |
| `service/samples/eevee_style_bowl_base.py` | **Function plus character**: a soft revolved body with a generated fur collar of cupped leaves, two ears and a tail. One script, three printed pieces, a `part` selector. |

---

## 1. The PARAMS contract

Full wire contract: `docs/architecture.md` → *PARAMS block convention*. The
short form:

```python
from build123d import *      # the house style
import forge_lib             # already bound in the namespace; the import is for readers

PARAMS = {
    "bar_length": {"value": 80.0, "unit": "mm", "min": 20.0, "max": 240.0,
                   "step": 1.0, "description": "Overall length of the bar"},
    "magnet_count": {"value": 3, "unit": "count", "min": 1, "max": 6, "step": 1,
                     "description": "How many magnets"},
}

def build(p):
    ...                      # returns a build123d Part / Solid / Compound
```

Rules, all enforced by the service:

- `value` **and** `unit` are required on every entry. `unit` is one of `mm`,
  `in`, `deg`, `count`, `ratio`, `bool`.
- Parameter names must be valid Python identifiers — the Blender panel turns
  them into properties.
- `min` / `max` / `step` / `description` are optional but write them anyway.
  `step` is a UI hint; the service never snaps to it.
- The declared `value` must sit inside its own `min`/`max`, or the script is
  rejected at parse time.
- Overrides outside `min`/`max` are **rejected, not clamped**.
- `build(p)` always works in **millimetres and degrees**. An `in` parameter is
  multiplied by 25.4 before `build` sees it, so never convert inches yourself.
- `build(p)` is called with a plain dict. Return one solid; a `Compound` of
  disjoint solids is legal but the checks will treat it as one part.

Declare a range only if **every value in it is buildable and printable** — the
whole range is a promise. But *printable* is not the same as *bed-sized*. A part
is designed at the size it should be, and a `max` that overflows the bed is fine
as long as the service can cut it up: `bed_fit` then reports a split, which is
print planning, not a bug in the script. What a range must never contain is a
value that fails `min_wall`, `overhangs` or `watertight`, or one that is too big
**even segmented** (`suggested_segmentation.feasible: false`) — that one really
is unprintable, and the `max` has to come down.

The honest exception: if splitting would wreck the design — a part whose whole
point is being one continuous piece, a surface that must not carry a seam — then
the bed *is* the limit for that part. Say so in the parameter's `description` and
size the `max` to the bed with its margin (Centauri Carbon: 256 mm cubed, 5 mm
margin per side, so 246 × 246 × 256 mm usable).

---

## 2. Printability rules

From `templates/printer.json` (Elegoo Centauri Carbon, the default profile; the
service mirrors it in `service/printer.py`):

| Number | Value | What it means for the script |
|---|---|---|
| `min_wall_thickness` | 0.8 mm | Hard floor. Anything thinner is a `min_wall` **fail**. |
| `min_feature_size` | 1.0 mm | Soft floor. Between 0.8 and 1.0 is a `min_wall` **warn**. Aim above this. |
| `max_unsupported_overhang_deg` | 50° | Angle **from vertical**: 0° is a wall, 90° is a flat ceiling. Steeper than 50° needs supports. |
| `nozzle_diameter` | 0.4 mm | Two perimeters is 0.8 mm — that is where `min_wall` comes from. |
| `bed` | 256 × 256 × 256 mm | Past this, `bed_fit` fails and proposes segmentation. Informational: the part prints in pieces. Not a size to design down to. |
| `tolerances.press_fit` / `slide_fit` / `loose_fit` | 0.1 / 0.2 / 0.3 mm | Clearance on a mating face. |
| `tolerances.magnet_pocket_extra` | 0.05 mm | Growth on a magnet pocket. |

### The rules, in the order they bite

1. **No knife edges — every taper ends on a land.** This is the failure that
   motivated `forge_lib`. A cone that runs into a flat face at an acute angle
   leaves a rim of material that is a fraction of a millimetre thick for its
   first layer. It looks like a clean chamfer and comes back as
   `min_wall: fail, thinnest 0.076 mm`. Give the acute end a straight vertical
   land of at least `max(min_land, min_wall)` — or just call
   `forge_lib.blunted_taper()` / `forge_lib.flared_lip()`, which do it for you.
   Every profile point in a revolve should be part of a pair that makes a
   straight segment; a lone vertex where two slopes meet is a knife edge.

2. **Relief is subtracted, never added.** An added rib is a free-standing
   feature that has to be a nozzle wide on its own and has nothing to hold it.
   A groove only has to leave enough wall behind, which is a condition you can
   actually enforce. Depth clamp, in this order:
   `min(depth, 0.40 × wall, wall − min_wall, 0.45 × surface_width)`.
   `forge_lib.textured_band()` applies all four.

3. **Flat-bottom bias.** Model the part standing on Z = 0 in its print
   orientation. A flat face on the bed prints; a curve tangent to the bed does
   not. Bottom **chamfers** at 45° are fine; bottom **fillets** are not — a
   fillet goes through 90° right where it meets the plate.

4. **Blind pockets open upward.** A pocket bored from the underside is a flat
   ceiling: a 90° overhang and an `overhangs` failure. Same for a counterbore —
   wide-at-top is safe, wide-at-bottom is a ceiling.

5. **In a solid, wide-at-top overhangs; in a hole, wide-at-bottom overhangs.**
   The sign flips between adding and cutting, and it is the single thing scripts
   get backwards most often. A countersink (a hole widening upward) faces
   *upward* and needs no support at all. `blunted_taper(..., role="cut")`
   knows this; `role="add"` is the default.

6. **Horizontal holes and bridges.** A hole through the X or Y axis has a
   ceiling at its crown. Teardrop it, or turn it into a vertical hole, or accept
   the warning knowingly.

7. **Bed limits are print planning, not a design rule.** Anything over ~246 mm
   in a horizontal axis or 256 mm tall will fail `bed_fit`. Do not try to fix
   that in the script, and do not shrink the part to make it go away — a part is
   modelled at the size it is meant to be, and being bigger than one plate is
   what segmenting is for.
   `bed_fit.data.suggested_segmentation.mode` is directly valid as `/segment`'s
   `mode`. Expect radial cuts for ring-like parts and planar Z cuts otherwise.
   The only genuine failure here is `suggested_segmentation.feasible: false` —
   too big even in pieces. That one needs a smaller part or a different design,
   and it is a conversation with the user, not a silent clamp.

8. **Tolerance belongs on the negative.** The peg is nominal, the socket is
   grown. Never shrink the male part to make a fit.

9. **Never let a computed thickness land *exactly* on a limit.** `min_wall`
   compares with a strict `<`, so a floor computed as
   `thickness - pocket_depth` that should be exactly 1.0 mm comes out as
   0.9999999999999996 and warns. Derive from a value with headroom
   (`floor = max(wall, land)`, not `floor = land`) or add a tenth of a
   millimetre. The same applies to any land you size at exactly `min_feature`.

### What the checks actually measure

`min_wall` casts a ray **inward along each facet normal** and reports the first
hit (`service/checks.py`). Consequences worth knowing:

- It is why a knife edge fails: on a surface that leans inward as it rises, the
  inward normal points down and exits through the bottom face after a distance
  proportional to the facet's height above the bed. The lowest band of facets
  measures almost nothing.
- It measures along the normal, not the shortest path, so a wedge thin across
  some *other* axis reads thicker than it prints.
- A narrow gap between two faces reads as a thin wall. That is conservative, but
  the number it reports is a gap, not a wall.
- Treat a fail as "look here", not as a dimension. The authoritative numbers are
  in `PARAMS`.

`overhangs` is warn-only and evaluates all six axis-aligned orientations,
suggesting the best. A part that only warns is shippable; a part that passes is
better.

---

## 3. The `forge_lib` catalog

Available in every script: the name `forge_lib` is pre-bound in the namespace
and `import forge_lib` also works. Every helper takes millimetres and an
optional `printer=` dict (partial profile, merged over the Centauri Carbon
default). Every helper has a `*_plan()` twin returning the numbers it will build
with plus a `clamped` list naming everything it moved and why. Nothing is
silently made sub-minimum: it clamps and says so, or raises
`forge_lib.PrintabilityError` (an HTTP 400 with a plain sentence).

### Printability features

| Call | Returns | Guarantees | Use it when |
|---|---|---|---|
| `blunted_taper(bottom_r, top_r, height, *, role="add"\|"cut", bore_r=0, taper_height=None, min_land_mm=None, support_free=True, printer=None)` | solid frustum, axis +Z, base on Z=0 | Thin end is a land ≥ `max(min_land_mm, min_wall)` wide, never a point; the acute end gets a straight vertical land of the same size; slope clamped to the overhang limit in whichever direction `role` says is dangerous; with `bore_r`, the wall stays ≥ `min_wall` or it raises | Any cone, chamfer, countersink, draft or tapered plinth. **The default answer to a `min_wall` failure on a sloped face.** |
| `flared_lip(inner_r, wall, height, flare, *, direction="down"\|"up", taper_height=None, min_land_mm=None, support_free=True, printer=None)` | annular collar, base on Z=0 | `wall` clamped up to `min_wall`; the flared end finishes on a vertical land ≥ min land; `direction="up"` is given the run to stay inside the overhang limit, or the flare is reduced | A footed collar, a ring's flared base, a rim that widens at the top |
| `textured_band(solid_face_radius, height, count, depth, style="flute"\|"scallop"\|"chevron", *, wall=None, z_bottom=0.0, pitch_fraction=0.78, chevron_deg=45, support_free=True, printer=None)` | `Compound` of cutters — **subtract it** | Depth clamped to `min(depth, 0.40·wall, wall−min_wall, 0.45·surface_width, 0.45·radius)`; cutter radius solved from the pitch so a flat land always survives between elements; suppressed to an empty compound (a no-op subtraction) when under 0.15 mm; `flute` cones its ends so the groove hangs no ceiling; `chevron` clamps its tilt into the printable window; `scallop` reports `support_free: False` | Decoration on a cylindrical face. Pass `wall` or the wall-based clamps cannot apply. |
| `feet_ring(outer_r, height, count, style="pad"\|"pier", *, foot_size=None, foot_depth=None, inset=0, chamfer=True, printer=None)` | positive solid, base on Z=0 | Each foot ≥ `min_feature` across with ≥ `min_feature` of air between neighbours (size clamped, impossible counts raise); nothing reaches past `outer_r`; optional 45° bottom chamfer, dropped if the profile is stricter | Discrete feet under a base |
| `arcade_base(outer_r, height, count, style="pier"\|"pad", *, inner_r=0, arch="pointed"\|"round", opening_fraction=0.6, sill=None, printer=None)` | positive plinth ring, base on Z=0 | Piers never under `min_feature`; a `min_wall` of material always left over the arch crown (the opening narrows rather than breaking through); `arch="pointed"` sits exactly on the overhang limit and genuinely passes, `"round"` prints fine but the facet check flags its crown and `plan["support_free"]` says so | An openwork base: legs with arches between them |
| `magnet_pocket(diameter, depth, printer=None, *, tolerance=None, available_depth=None)` | negative cylinder, mouth at Z=0 facing **up** — **subtract it** | Pocket is `diameter + 2 × magnet_pocket_extra` by `depth + magnet_pocket_extra` — identical to `/segment`'s magnet joints; the mouth overshoots 0.2 mm so the boolean is never coplanar; with `available_depth` it raises rather than leaving a floor under `min_wall` | Any disc magnet. `diameter`/`depth` are the magnet's numbers off the packet. Always pass `available_depth`. |
| `shell_box(length, width, height, wall, *, floor=None, open_top=True, corner_r=0, printer=None)` | hollow box, base on Z=0 | `wall` and `floor` clamped up to `min_wall`; a cavity the walls would swallow raises with the size the box needs to be; `open_top=True` is support-free, `open_top=False` reports `support_free: False` (a lid is a 90° ceiling); a fillet the kernel refuses falls back to square and records it | Trays, boxes, enclosures |
| `wall_safe_shell(solid, wall, *, openings=None, printer=None)` | hollowed solid | `wall` clamped to `min_wall`; a hollowing the kernel cannot do raises a plain message instead of an OCC error | Hollowing a shape that is not a box. Prefer `shell_box` when it is one. |
| `screw_boss(screw_diameter, height, *, wall=None, hole_depth=None, style="thread-forming"\|"clearance", printer=None)` | solid boss, base on Z=0 | Wall ≥ `min_wall` (default `max(0.5·D, 2·min_wall)`); a floor of ≥ `min_wall` always under the hole; a hole clamped up to `min_feature` so it cannot print closed; support-free by construction | A screw post inside an enclosure |

### Ornament — decoration that is generated, not sculpted

The gap these close: asked for "a fur collar of overlapping leaves on a soft
rounded body, two ears and a tail", a generator that only has cylinders and
grooves produces a cylinder, a dashed groove and two flat slabs. None of that is
sculpture, though — a body is **a silhouette turned about an axis**, a fur
collar is **one leaf arrayed round a ring**, and an ear is **a silhouette with
thickness**. All three are parameter sets, so all three belong in the script.

A flat element is a **prism** — a smooth closed outline extruded along its own
normal — so every convex edge is 90° whatever the outline does. A cupped one is
that outline with an arched front over its flat back, and its section is never
*thinner* than the thickness asked for. Neither is a stylistic choice:
`min_wall` casts rays inward along facet normals, so a convex edge under 90°
measures `distance-from-the-edge × tan(angle)`, which goes to zero as the
tessellation gets finer. It is the knife-edge rule from §2 in plan view.

**Flat reads as cut-outs; cupped reads as fur or feathers — default to cupped
for an organic reference.** `cup=` arches each element's front over its flat
back, and it is the single difference between "a ring of paper blades" and "a
collar".

| Call | Returns | Guarantees | Use it when |
|---|---|---|---|
| `leaf_collar(ring_radius, leaf_length, leaf_width, count, overlap=0.3, droop_deg=20, thickness=None, jitter=0.0, seed=0, printer=None, *, clearance=None, tip_land=None, layers=2, cup=0.0, shape="leaf")` | one solid ring, base on Z=0 | **One watertight solid** at any count/overlap/jitter/cup — every root is buried in a band, so nothing can float; leaves sit on two ranks a *solved* distance apart so alternate ones lie **over** their neighbours instead of grazing them; each leaf ends on a straight land ≥ `1.5 × min_feature`, never a point; `droop_deg` (lean **from vertical** — the same convention the overhang check uses, and it *is* the underside's overhang angle) clamped into the printable window; `jitter` never leans an element less far than you asked, so the plan's verdict is exact | A fur collar, a ruff, feathers, a mane, petticoats — anything that reads as overlapping organic elements round a body |
| `petal_crown(ring_radius, petal_length, petal_width, count, overlap=0.15, flare_deg=25, ..., cup=0.0)` | one solid ring, base on Z=0 | Same as `leaf_collar`, **plus**: a crown is genuinely support-free. Its band sits on the plate, the petals lean out as they *rise*, and their tip lands face upward — so `overhangs` passes rather than warning. It is also the one direction that pays for a cup: a crown's arched face is the one pointing down, so the cup's climb out of the root is charged to the flare window | A crown, an upright ruff, a flower, a fan of fins, spikes on a lid |
| `scale_band(ring_radius, scale_length, scale_width, count, rows=3, overlap=0.35, droop_deg=30, ..., cup=0.0)` | one solid ring, base on Z=0 | Same as `leaf_collar`, with rows: each row is offset half a pitch and its spacing is **floored at the drop that lets it clear the row above** (a drooping element travels outward as it falls, which is what a conical band gives it for free) | Dragon hide, pine cones, fish, armour, roof tiles |
| `silhouette_part(points, thickness, rounding=None, taper=0.0, peg=None, printer=None)` | one solid, lying flat on the bed | 6–16 `[x, y]` control points splined into **one smooth closed outline** and extruded, so the part is watertight and every side face is vertical (nothing but the bed-facing bottom points down); fewer than 6 points, more than 16, or an outline that crosses itself is refused with a plain sentence; `thickness` clamped to `min_wall`; `taper` (degrees of draft, thinner at the top) **stepped down** until the outline survives it; `rounding` softens the **top** perimeter only and is **stepped down** until the kernel accepts it, reporting `rounding_achieved_mm`; `peg={"d","l"}` attaches a keyed `peg()` at the outline's bottom-centre pointing −Y | Ears, tails, fins, wings, horns, crests, leaves — any appendage you would otherwise sculpt |
| `soft_body(profile_points, wall=None, *, floor=None, base_land=None, printer=None)` | one body of revolution, base flat on Z=0 | 5–10 `(radius, z)` control points splined into one silhouette, **sampled into a fine polygon** and revolved; `wall=None` gives a solid, `wall=3.0` gives a constant-wall hollow with an **open top** (a lid is a 90° ceiling); base clamped up to a `min_land` disc so nothing is tangent to the bed; `z` must strictly increase, checked on the control points and again on the samples; a wall that would close the body up raises; a silhouette that swells outward past the overhang limit, or a cavity that closes in over itself, is **named in `plan["unsupported"]`** with the angle and which point to move | **The body a collar sits on.** Bowls, vases, planters, pots, creature bodies — anywhere you would otherwise reach for a cylinder |

The things to know before turning these knobs:

1. **`cup` is the knob that makes the difference.** `cup=0.32` arches each
   element's front to a third of its half-width above its own edges. The
   section is never thinner than `thickness` — `thickness` at the edges,
   `thickness + rise` at the crown — so `min_wall` holds by construction, and
   more strongly than a constant-wall shell would. Read
   `plan["cup_rise_mm"]`, `plan["cup_flank_deg"]` and `plan["stack_mm"]` (how
   far an element now stands off its own root plane; every envelope in the
   collar is built from it). A cup pushes the ranks apart and the band out, so
   expect `band_outer_radius_mm` to grow with it.
2. **`droop_deg` has a sweet spot: 42–48°.** The leaf's *underside* must lean no
   further than the overhang limit; its *tip land* faces down the leaf's own
   axis and so must lean no *less* than 90° − limit. Both hold only in that
   window, and `leaf_collar_plan(...)["unsupported"]` says so in as many words
   when you are outside it. It is also roughly the flare a real ruff has.
   Cupping a **collar** costs nothing here — its arched face points outward and
   up, and the face the check measures is still the flat back. Cupping a
   **crown** does, and `plan["cup_slope_deg"]` is the number it costs.
3. **A collar you union onto a base wants a negative `clearance`.** The default
   is a positive slide fit, for a ring that slips over a separately printed
   cylinder. Union one on at `clearance=+0.2` and you get two solids 0.2 mm
   apart. Pass `clearance=-1.0` and the band bites into the base — one solid,
   and the band's bottom rim (the one face the plan always flags) ends up
   *inside* the part where nothing can see it. On a `soft_body` the bore has to
   bite at the **narrowest** the body gets over the band's whole height, not at
   its radius under the band's top — see pattern C.
4. **`layers=2` is what makes a collar dense, and it only works because the
   ranks shingle.** The gaps between one rank's elements are two pitches wide
   and no element can be that wide, so a single rank always shows the band
   between its elements. The second rank sits in those gaps — stepped along the
   elements' own normal, out *and* up, so it lies over the inner rank instead
   of hiding behind the band's skirt. `layer_step_r_mm`, `layer_rise_mm` and
   `layer_z_mm` in the plan are where that step ends up.

Read the plan before you build:

```python
plan = forge_lib.leaf_collar_plan(r_out, 26.0, 20.0, 16, droop_deg=44.0, cup=0.32)
plan["height_mm"]        # so you can place the collar by its band, not by guesswork
plan["band_height_mm"]   # the band alone: the span whose bore has to bite the body
plan["clamped"]          # "width 20 -> 26.60 mm: 16 leaves on a ... circle need that much"
plan["support_free"]     # False, and then:
plan["unsupported"]      # [{"what", "angle_from_vertical_deg", "area_mm2", "why"}, ...]
```

### Numbers and the profile

| Call | Gives you |
|---|---|
| `profile(printer=None)` | The resolved printer dict |
| `min_wall()` / `min_feature()` / `max_overhang_deg()` | 0.8 / 1.0 / 50 on the default profile |
| `min_land(min_land_mm=None)` | `max(min_land_mm, min_wall)`, defaulting `min_land_mm` to `min_feature` |
| `fit_tolerance("slide_fit")` | One named clearance |
| `max_flare_for(taper_height)` | How far a surface may lean out over that height and still self-support |

### Appendage slots (pre-existing)

| Call | Does |
|---|---|
| `peg_spec(d=6, l=8, key=True, ...)` | The single source of truth for one peg/socket pair |
| `peg(spec)` | The keyed peg — a positive, axis +Z, base on Z=0 |
| `socket_for(spec, tolerance=0.2)` | The matching negative, grown by `tolerance` on every mating face |

### Composition idioms

```python
part = forge_lib.soft_body(points)                       # the body, solid
part += forge_lib.feet_ring(r_out, 6.0, 4)               # positives: union
part -= forge_lib.textured_band(r_out, 20.0, 24, 1.6,    # negatives: subtract
                                wall=wall, z_bottom=8.0)
part -= Pos(x, y, top_z) * forge_lib.magnet_pocket(6.0, 3.0, available_depth=top_z)
```

A `soft_body` is an ordinary solid; bores, seats, sockets and pockets go onto it
exactly as they would onto a box. Read `plan["height_mm"]` and
`plan["max_radius_mm"]` to place them:

```python
plan = forge_lib.soft_body_plan(points)                  # or (points, wall=3.0)
body = forge_lib.soft_body(points)
body -= Pos(0, 0, plan["height_mm"] + 0.5) * Rot(180, 0, 0) * \
    forge_lib.blunted_taper(bore_r, bore_r, depth, role="cut")
body -= Pos(x, y, plan["height_mm"] + 0.5) * Rot(180, 0, 0) * \
    forge_lib.socket_for(spec, forge_lib.fit_tolerance("slide_fit"))
```

Read a clamp back when you need to explain it to the user:

```python
plan = forge_lib.textured_band_plan(r_out, 20.0, count, depth, wall=wall)
if plan["clamped"]:
    ...   # each entry is "what: old -> new mm: why"
```

---

## 4. The workflow law

1. Write the script from helpers. Do not write raw cone, wedge or thin-plate
   geometry when a helper covers it.
2. **Always run `/check` after generating.** Not `/generate` — `/check`. A part
   that has not been checked is not finished.
3. Read the failures in this order and act:

   | Failure | First move |
   |---|---|
   | `min_wall` on a sloped or tapered face | Replace the raw revolve/loft with `blunted_taper` or `flared_lip`. This is the common case. |
   | `min_wall` on decoration | Replace the added ribs with `textured_band`, or pass `wall=` so its clamps can apply. |
   | `min_wall` on an outline you extruded yourself | Almost always a convex edge under 90° in **plan** view, or a spline outline extruded straight (OCC's mesh of a B-spline side wall pinches inside its own cap). Use `silhouette_part`, which splines the outline and then samples it into a polygon before extruding. |
   | Decoration that came out as a groove or a slab | It is not sculpture. A collar of overlapping elements is `leaf_collar` / `petal_crown` / `scale_band`; an ear, tail, fin or wing is `silhouette_part`. See pattern C. |
   | Decoration that reads flat, stiff or sparse | The elements are cut-outs. Pass `cup=0.3` and leave `layers=2`: flat reads as cut-outs, cupped reads as fur or feathers. Then check the body it sits on is a `soft_body` and not a cylinder. |
   | `overhangs` on a collar's band rim after the body stopped being a cylinder | The band's bore is a cylinder; size `ring_radius` to the **narrowest** the body gets over `band_height_mm`, not to its radius under the band's top. |
   | `min_wall` on a pocket floor | Pass `available_depth=` to `magnet_pocket`, and derive the part's thickness from the pocket depth rather than the other way round. |
   | `min_wall` on a shell | `shell_box` / `wall_safe_shell`, and let it clamp the wall. |
   | `overhangs` on a base | `arcade_base(arch="pointed")` or `feet_ring`. |
   | `overhangs` on a cone or hole | Check `role`: a solid flares dangerously upward, a hole dangerously downward. Then let `support_free=True` clamp it. |
   | `overhangs` with a better orientation offered | If the best orientation is not `+Z`, rebuild the part standing that way up — the model should be in its print orientation. |
   | `bed_fit`, with a feasible `suggested_segmentation` | **Nothing.** This is not a failure to fix — the part is the size it should be and prints in pieces. Hand `suggested_segmentation.mode` to `/segment` at print time and say so in the reply. Never shrink a `max` to make this go away, and never segment inside the script. |
   | `bed_fit`, with `suggested_segmentation.feasible: false` | The real one: too big even segmented. Shrink the declared `max` or rework the shape — and put the choice to the user rather than deciding it silently. |
   | `watertight` | Almost always a coplanar-face boolean. Make every cutter overshoot the face it enters by 0.2–1 mm. |

4. **Iterate at most three times.** After the third check, stop and explain in
   plain language what is still failing, which number causes it, and what the
   user would have to change (a thicker wall, a shorter part, supports in the
   slicer). Do not keep tweaking; a fourth attempt is a design problem, not a
   coding one.
5. Report the check result to the user in plain terms — "all four pass" or "it
   prints, but the underside of the lid needs supports" — never as raw JSON.
6. **No mystery geometry.** Every feature the script adds that the user did not
   name — socket lugs, a foot ring, a stiffening rib, a drain hole — must be
   (a) *named in the reply with its purpose* ("the two small columns on the rim
   are sockets the ears plug into") and (b) *removable by a parameter*: counts
   go to 0 (`feet_count`, min 0), toggles exist (`ear_sockets: bool`), nothing
   structural is hard-coded. A user staring at an unexplained column has lost
   trust in the whole part. When in doubt whether a feature is wanted, add it
   OFF by default and mention the switch.
7. **Multi-part results arrive seated, separate, and named.** Companion pieces
   (ears, collar, tail) are their own objects — never fused into each other —
   positioned where they belong on the core (ear pegs IN their sockets, collar
   seated at its band height), in one collection named for the project. The
   user must be able to delete any proposal object and lose nothing else, and
   ask for an exploded layout only if they want one.

---

## 5. Three worked patterns

### A. The flat bar — `service/samples/magnet_holder.py`

Shape: a bar lying on the bed with a row of blind pockets, rounded ends, and
countersunk mounting holes. The pattern generalises to any flat plate with
holes: hooks, brackets, mounting strips, tool holders.

The moves, in order:

1. **Ask the library for the feature sizes first.**
   ```python
   land   = forge_lib.min_land(p["min_land_mm"])
   wall   = max(p["wall"], forge_lib.min_wall())
   pocket = forge_lib.magnet_pocket_plan(p["magnet_diameter"], p["magnet_thickness"])
   ```
2. **Derive the body from those sizes, never the reverse.** The requested
   thickness is a floor, not a fact:
   ```python
   thickness = max(p["bar_thickness"], pocket["pocket_depth_mm"] + max(wall, land))
   spacing   = max(p["magnet_spacing"], pocket["pocket_diameter_mm"] + wall)
   width     = max(pocket["pocket_diameter_mm"], mount_d) + 2 * wall
   ```
   No slider combination can now put a pocket through the back or merge two
   pockets into a slot.
3. **Rounded ends with a stadium, not a fillet.** A box plus a cylinder at each
   end gives round ends whose faces are all vertical. A fillet on the bottom
   edge would be a 90° overhang at the bed.
4. **Pockets from the top, with `available_depth`.**
   ```python
   bar -= Pos(x, 0, thickness) * forge_lib.magnet_pocket(d, t, available_depth=thickness)
   ```
5. **One cutter for the hole and its countersink.** The straight land is the
   through bore, the taper above it is the countersink, and the cutter finishes
   above the top face so the boolean is never coplanar:
   ```python
   bar -= Pos(x, 0, -1.0) * forge_lib.blunted_taper(
       mount_r, mount_r + taper_h, thickness + 1.0 + 0.5,
       role="cut", taper_height=taper_h)
   ```
   `role="cut"` is what tells the helper that widening *upward* is the safe
   direction here.

Result: `bed_fit`, `min_wall`, `overhangs` and `watertight` all **pass** at the
defaults, at the small extreme (1.2 mm wall, 3 mm bar, six 3 mm magnets, 8 mm
screw holes) and at the large one (6 mm wall, 25 mm bar, six 20 mm magnets at
30 mm pitch — a 222 mm bar that still clears the plate margin).

### B. The ring — `projects/bowl-holder/part_base_ring.py`

Shape: a body of revolution with a decorative band and an openwork base. The
pattern generalises to collars, vases, planters, lampshades, coasters, any
"round thing with a wall".

1. **Build the wall as a revolved `(radius, z)` polygon.** One profile carries
   the bore, the seat, the rim and the foot. Every vertex where two slopes meet
   must instead be two vertices with a straight segment between them — that is
   the knife-edge rule written as a polygon. See `_seat_profile_points`, whose
   `land` and `lip_land` points exist only for that.
   Prefer `forge_lib.flared_lip()` for the foot; write the polygon by hand only
   when the section is genuinely more complicated than a collar.
   For anything that should read as *soft* rather than machined, build the
   outside with `forge_lib.soft_body()` and cut the inside out of it: five or
   six control points give a silhouette a polygon cannot, and the helper keeps
   the base flat, the wall constant and the swell inside the overhang limit.
2. **Cut the band, never add it.** The bowl-holder's `_flute_cutters` is the
   proven original of `forge_lib.textured_band`: the effective depth is
   `min(depth, 0.40 × wall, 0.45 × surface_width)` and the cutter radius is
   solved from the pitch as
   `R = (width² / 4 + d²) / (2 d)` for `width = 0.78 × pitch`,
   which guarantees a flat land between flutes at every count. Use the helper;
   read the original when you need to understand what it is doing.
3. **Openwork base with `arcade_base`**, or subtract arch cutters as the
   bowl-holder does. Pointed arches pass the overhang check; round ones bridge
   in practice but get flagged.
4. **Lugs and sockets last**, after the subtractions, so a boolean never has to
   resolve a cutter against a feature that is about to be added. Overshoot every
   through-cut past the face by ~1 mm.
5. **Clamp proportions to the total height**, so no combination of sliders can
   collapse the profile into a self-intersecting polygon — and raise a plain
   `ValueError` (a 400) if one somehow does.

### C. Hybrid designs: function plus character — `service/samples/eevee_style_bowl_base.py`

Shape: a part that has a job **and** a face. A dog-bowl holder that is also a
creature; a pen cup with ears; a planter shaped like an animal; a lamp base with
fins. The pattern generalises to anything where a user asks for a real object
"but make it look like X".

This is the pattern that used to fail, and it is worth being honest about how —
twice.

Given a reference image of a bowl holder with a fur collar and ears, a generator
that only knows cylinders and grooves produces a correct bowl ring, a **dashed
groove** where the fur should be, and **flat slabs** for the ears. The base was
never the problem. The decoration was — and the fix is not to sculpt it, it is
to notice that the decoration is parametric too.

Then the ornament helpers landed, every check passed, and the result still read
as **stiff, sparse and flat**: flat leaves are cut-outs, and a cylinder is not a
body. Passing four checks is necessary and it is not the brief. The fix for that
was two more parameters — `cup=` on the collar and `soft_body` under it — and
one measurement that a screenshot would not have given: rays cast out of the
band's axis, at the gaps between one rank's leaves, to prove the second rank
really covers them.

1. **Split the design into a base with sockets and appendages with pegs.**
   The base does the work (holds the bowl, stands up, does not tip). The
   character lives in pieces that plug into it. One `peg_spec` is the single
   source of truth for both halves:
   ```python
   spec = forge_lib.peg_spec(d=p["peg_diameter"], l=p["peg_length"])
   base -= Pos(x, y, top) * Rot(180, 0, 0) * forge_lib.socket_for(spec, tol)
   ```
   Sockets open **upward** in the base; pegs lie in the appendage's own plane.

2. **The body is a `soft_body`, not a cylinder.** A collar's band is a cone;
   against a straight wall a cone reads as a hat brim, and against a body that
   already swells and tapers it reads as the body's own shoulder. Five or six
   `(radius, z)` proportions are the whole silhouette:
   ```python
   points = [(r_out, 0), (r_out + 0.55*swell, 0.16*h), (r_out + swell, 0.45*h),
             (r_out + 0.80*swell, 0.70*h), (r_out + 0.25*swell, 0.90*h), (r_out, h)]
   part = Pos(0, 0, base_z) * forge_lib.soft_body(points)   # solid; bore it after
   part -= revolve(Plane.XZ * Polygon(*cavity_points, align=None), axis=Axis.Z)
   ```
   Bores, seats, sockets and pockets go on afterwards exactly as they would on
   any other solid; there is nothing special about a revolved body. Clamp the
   swell — a silhouette that leans outward as it rises **is** an overhang, and
   `soft_body_plan(...)["unsupported"]` will say so.

3. **Fur, feathers, a mane, a ruff → `leaf_collar`, cupped.** One leaf, arrayed:
   ```python
   args = dict(ring_radius=narrowest, leaf_length=26.0, leaf_width=18.0, count=16,
               overlap=0.35, droop_deg=44.0, jitter=0.35, seed=3,
               cup=0.32,                   # arched front: fur, not cut-outs
               clearance=-1.0)             # negative: bite into the body
   plan = forge_lib.leaf_collar_plan(**args)
   part += Pos(0, 0, band_top - plan["height_mm"]) * forge_lib.leaf_collar(**args)
   ```
   Read `plan["height_mm"]` and place the collar by it rather than guessing.
   `jitter` is what stops sixteen identical leaves reading as a machined ring;
   `seed` makes "organic" reproducible; `cup` is what stops them reading as
   paper.

   On a `soft_body`, `ring_radius` is the **narrowest the body gets over the
   band's own height** — not its radius under the band's top. The band's bore is
   a cylinder, so if the body shrinks away from it anywhere along the band, the
   band's bottom rim stops being buried and comes back as 535 mm² of 90°
   overhang. Two passes: plan once for `band_height_mm`, sample the silhouette
   over that span, plan again.

4. **Ears, tails, fins, wings → `silhouette_part`.** Draw the outline as **8–12
   proportions**, never a pixel trace:
   ```python
   _EAR_SHAPE = [(0.00, 0.000), (0.36, 0.085), (0.50, 0.340), (0.40, 0.660),
                 (0.13, 0.885), (0.00, 1.000), (-0.20, 0.830), (-0.43, 0.490),
                 (-0.47, 0.170), (-0.27, 0.028)]     # (u across, v along)
   ear = forge_lib.silhouette_part(
       [[u * width, v * length] for u, v in _EAR_SHAPE],
       thickness, rounding=1.6, peg={"d": peg_d, "l": peg_l})
   ```
   Ten numbers *are* the ear. The artist moves one and gets a different ear that
   is still printable and still fits the same socket — which is the whole point
   of a parameter set, and the reason tracing an image would be a step
   backwards. Read the proportions off the reference the way you would read a
   measurement: tip here, widest point there, root that wide.

5. **One script, one `part` selector, one `/check` per piece.** `build()` must
   return one thing, and these pieces have different print orientations (the
   base stands up; the ear and tail lie flat). A `Compound` of all three would
   force one orientation on all of them and make the overhang answer
   meaningless. So:
   ```python
   "part": {"value": 0, "unit": "count", "min": 0, "max": 2,
            "description": "0 the base, 1 an ear, 2 the tail"},
   ```
   Check each value separately and report each separately.

6. **Spend the printability budget where it shows.** Three decisions carried
   this sample from "warns everywhere" to "passes":
   - `droop_deg = 44`, inside the 42–48° window where a drooping leaf is
     support-free at *both* ends. Below 42 the leaf tips point too far down;
     above 48 the undersides do. It is also about the flare the reference has,
     so printability and the drawing agreed for once.
   - Socket lugs built as **ribs running down into the collar band**, not
     bosses hanging off the rim. Three bosses left 354 mm² of flat, 90°
     underside in mid-air; three ribs leave none.
   - The collar's bore sized to the body's **narrowest point under the band**.
     Sizing it to the body's radius under the band's *top* — which is the same
     number on a cylinder and not on a soft body — left the band's bottom rim
     hanging in mid-air: 535 mm² at 90°.

Result: the base passes all four checks with the collar on it, cupped leaves,
swollen body and all. The ear and the tail pass three and warn at 63–80 mm² —
the underside of the peg, which is a horizontal cylinder however you draw it,
and which bridges at 6 mm.

---

## 6. Checklist before returning a script

- [ ] `PARAMS` entries all have `value` + `unit`; names are identifiers.
- [ ] Every declared `min`/`max` combination builds **and** passes the checks.
- [ ] Modelled in the print orientation, sitting on Z = 0.
- [ ] No raw taper, cone or chamfer that a helper covers.
- [ ] Every cutter overshoots the face it enters.
- [ ] Blind pockets open upward; no flat ceilings.
- [ ] Decoration is generated, not sculpted: no hand-rolled ribs, slabs or
      dashed grooves where an ornament helper covers it.
- [ ] Anything organic is **cupped** (`cup=0.3`) on a **`soft_body`**, not flat
      elements on a cylinder.
- [ ] Multi-piece designs expose a `part` selector and each piece is checked.
- [ ] `/check` run, all four results seen, reported in plain language.

---

<!-- BEGIN maker section (Phase 10 / maker_lib). Owned by maker mode; append
     below this marker only, and leave the sections above untouched. -->

## 7. Maker components and mechanisms

Everything above makes a shape the printer can hold. This section is about
making a shape the **world** fits into: a switch a finger can operate, an LED
that stays where you pressed it, a cell you can change, and the circuit that
joins them.

Two new modules, installed in a script's namespace exactly the way `forge_lib`
is:

```python
from build123d import *
import forge_lib, maker_lib      # both already bound; the import is for editors

PARAMS = {"wall": {"value": 2.0, "unit": "mm"}}

def build(p):
    body = forge_lib.shell_box(40, 40, 30, p["wall"])
    body -= Pos(0, 0, 30) * maker_lib.cutout("led_5mm", depth=p["wall"])
    return body
```

`components` (the data table) and `wiring` (the circuit maths) are bound too,
but `maker_lib` re-exports everything you normally need, so one import is enough.

### 7.1 The design-around-components law

**Pick the real part first. Then model to its dimensions.**

A cavity invented from nothing fits nothing. Before any geometry exists, choose
the actual switch, the actual cell holder, the actual LED — then let their
numbers set the model's numbers, not the other way round. Every derived
clearance comes from `printer.json`'s `tolerances`, never from a number typed
into a script.

Three habits follow from it, and all three show up in
`samples/push_lamp_core.py`:

1. **Ask the table, then build with what it gives back.** `component(name)`
   returns the record; `cutout_plan(name)` returns the negative's numbers with
   every fit named and sourced. A script that writes `+ 0.2` anywhere has
   stopped following the printer profile.
2. **Derive the body from the parts.** The push lamp's `body_radius` is not a
   style choice — it is whatever the coin-cell holder standing on edge beside
   the switch tower needs, and the script *raises with the number* when it is
   not enough. That refusal is the law working.
3. **Repeat the verify sentence.** Every record carries
   `verify_against_your_part`, and it is not boilerplate: clones vary by ±0.3 mm
   routinely, and coin-cell holders sold under one search term run from 20 to 28
   mm long. Put that sentence in front of the user before they print.

### 7.2 The component catalog

`maker_lib.catalog()` lists them; `maker_lib.catalog("switch")` filters by
category. Dimensions are datasheet-typical for the family, not a measurement of
one unit.

| Component | Category | The numbers that matter | Use it for |
| --- | --- | --- | --- |
| `tactile_6x6_latching` | switch | 6×6 mm body, 7.3 mm tall, **1.5 mm latch travel**, 250 gf, max overtravel 0.3 mm | Push on / push off. The switch behind a press-the-figure-to-light-it toy. |
| `tactile_6x6_h43` / `_h73` / `_h95` | switch | 6×6 mm body, 4.3 / 7.3 / 9.5 mm tall, **0.25 mm** travel, 160 gf, max overtravel 0.2 mm | Momentary. Lights only while held. The extra height is button, not travel. |
| `push_latching_12mm` | switch | 12 mm panel hole, thread 11.9 × 8 mm, 25 mm behind the panel, **3 mm** stroke, 500 gf | A big obvious latching press that mounts through a wall by itself. |
| `slide_switch_sk12` | switch | 7 × 3.5 × 3.5 mm body, 2.5 mm sideways throw, 3 pins on 2.54 | Plain on/off that cannot be pressed by accident. |
| `cr2032_cell` | power | Ø20 × 3.2, **3.0 V**, 220 mAh, ~30 Ω internal, comfortable at 3 mA | The default supply. Small, flat, changeable. |
| `cr2032_holder` | power | 26 × 24 × 6 envelope, pins on 20 mm, mount holes Ø2.2 on 20 mm | Holding that cell. **The loosest entry in the table — measure yours.** |
| `aaa_pair_box` | power | 52 × 26 × 14, 3.0 V, 1000 mAh, 150 mm leads | Same voltage, five times the run time — and it *will* deliver 20 mA, which is why the resistor stops being optional. |
| `led_3mm` / `led_5mm` / `led_10mm` | light | Lens Ø3 / 5 / 10; flange Ø3.4 / 5.8 / 11.0; legs on 2.54; 20 mA | The light. Buy **diffused** for anything glowing through a translucent print. |
| `m3_screw` | fastener | Thread Ø3.0, pan head Ø6.0 × 2.4, clearance 3.4, thread-forming 2.4 | Holding two printed pieces together. |
| `heat_set_m3` | fastener | 5.7 mm long, OD 4.6, **hole Ø4.0 × 6.2** | A real metal thread that survives being undone. |
| `magnet_5x2` / `6x3` / `8x3` / `10x2` | magnet | N35 discs, ±0.1 mm, 0.5–1.7 kg pull | Doors and lids. Goes through `forge_lib.magnet_pocket` unchanged. |

**LED forward voltages** (`maker_lib.led_forward_voltage(colour)`) are what the
resistor maths turns on: red / orange 2.0 V, amber / yellow 2.1 V, green / blue
/ white 3.0 V, UV 3.4 V, infrared 1.4 V. White is a blue die under phosphor,
which is why it has blue's forward voltage — and why a white LED runs straight
off a 3 V coin cell with no resistor at all.

### 7.3 envelope / cutout / mount

Three verbs per component. Each has a `*_plan()` twin listing every fit it chose
and where that fit came from.

- **`envelope(name)`** — the keep-out solid, grown by `loose_fit`. Use it as a
  *check*: subtract it from a draft body and if anything vanishes, something was
  in the component's way. Not a cutter.
- **`cutout(name, depth=...)`** — the negative you actually subtract. Placed
  exactly like `forge_lib.magnet_pocket`: it hangs below Z = 0 with its mouth
  poking `MOUTH_OVERSHOOT_MM` above, so you put it *at the face it is bored into*
  and it bores straight down.
- **`mount(name, style=...)`** — the printed positive that holds the part, built
  standing on Z = 0. Read `plan["usage"]`: it is one sentence saying how the
  positive and the negative compose, and "union the collar inside, bore from
  outside" is the kind of thing that is obvious once and never again.

The fits are chosen per face by what the joint has to do:

| Joint | Fit | Why |
| --- | --- | --- |
| LED lens bore | `press_fit` (0.1/side) | It has to grip. An FDM hole prints about that much undersize, so a 5.2 mm bore comes out near 5.0 — the plan says so, and says what to do if your printer holds holes true. |
| LED flange seat | `slide_fit` | It only has to seat, not grip. |
| LED leg relief | `loose_fit` | Legs must not be crushed against solid plastic. |
| Switch / holder body pocket | `slide_fit` | It must drop in without force. **Forcing a moulded body cracks it.** |
| Panel switch hole | larger of the datasheet hole and thread + 2×`slide_fit` | A printed hole needs the printer's clearance whatever the datasheet says. |
| Magnet pocket | `magnet_pocket_extra` | Straight through `forge_lib.magnet_pocket`. |
| Heat-set insert hole, M3 clearance hole | **none** | Those diameters are the insert maker's and ISO's. Adding our clearance would add clearance twice. |

Mount styles are per component: `collar` for LEDs, `shelf` / `pocket_boss` for
switches, `screw` / `rib` for holders and boxes, `boss` for fasteners.
`mount("cr2032_holder", style="rib", standing=True)` turns the holder on edge —
the difference between needing its whole 35 mm diagonal of floor and needing a
26 × 10 mm strip of it, which is the difference between a figure 70 mm wide and
one 48 mm wide.

Two refusals worth knowing about, because both are the library telling you
something true:

- A `cutout("push_latching_12mm", depth=...)` **raises** if the wall is thicker
  than the switch's thread can clamp. It names the range (1–4 mm) and suggests
  a local rebate.
- A `mount("heat_set_m3", style="boss")` does **not** go through
  `forge_lib.screw_boss`, and says why in its plan: that helper derives its hole
  from the screw and the printer, and a heat-set hole is neither. It is the
  insert maker's number and a deliberate interference — the brass melts its own
  way in and the displaced plastic is what grips it.

### 7.4 The push mechanic: `plunger`

A gap that makes a cap removable is not a mechanism. **This** is a mechanism:

```python
rig = maker_lib.plunger(6.0, switch="tactile_6x6_latching")
rig["guide"]     # positive: the sleeve, union it into the wall
rig["plunger"]   # positive: the sliding piece, its own printed part
rig["plan"]      # the kinematics, every number of it
```

Both solids come back **in one frame, in the at-rest position**, with Z = 0 at
the guide sleeve's bottom rim. Place them with one `Pos` and the assembly lands
together; `plan["switch_seat_z_mm"]` then says exactly where the switch's seat
has to be in that same frame.

What is engineered, rather than left as a gap:

- **Travel** is the switch's own actuation stroke plus overtravel — and the
  overtravel is **clamped down to what the switch can physically take**. Ask for
  0.5 mm on a latching 6×6 and you get 0.3 mm with a clamp note, because past
  that its button is already bottomed on its own body and the end stop would be
  crushing the switch instead of protecting it.
- **The end stop** is the cap's underside landing on the guide's top rim. That
  rim is why the switch can never see more than its stroke plus that overtravel
  however hard the press. A finger can put 5 kg through a plunger; the end stop
  is the only reason that does not reach the switch.
- **Guide engagement** is never under `2 ×` the stem diameter. A pin engaged less
  than that cocks in its bore and jams the first time somebody presses it
  off-centre, and the stem is longer than the sleeve over the whole travel, so
  the engagement is the sleeve's full length at every point of the press.
- **Retention** is a flange wider than the bore. Which means the plunger goes in
  **from the inside, before the body is closed** — `plan["assembly"]` says so, in
  order, and the decorative cap goes on last because it is what makes the
  plunger captive.
- **The return is the switch's own spring.** There is no second spring and there
  does not need to be. For a latching switch, `plan["return"]` spells out what
  that means: the button latches down and stays down, so the cap visibly sits
  1.5 mm lower while the light is on. That is a feature, not a fault — say so in
  the reply rather than letting the user discover it.
- **Free play** (0.3 mm) is the designed rattle. It exists so no stack-up of
  printed tolerances can leave the plunger *preloading* the switch, which is a
  model that is permanently on. Gravity closes it, so the plunger's resting place
  is on the button, and it is also exactly how far the plunger can rise before
  the flange catches.

Take the cap's socket from `plunger_cap_socket(plan)` so the press fit matches,
and put its shoulder at `plan["cap_underside_z_mm"]`. The cap does not have to
be a plug — in the sample it is a **cup** whose skirt comes down over the guide
and the LED, so neither is on show.

Two switches refuse a plunger, and the refusal is the right answer:
`slide_switch_sk12` moves sideways, and `push_latching_12mm` already *is* a
plunger — it clamps through the wall with its own nut, and putting a second
sliding mechanism in front of it buys twice the friction for nothing.

**The plan's numbers are also the demo.** Every kinematic field above is what a
mechanism demo animates, so a mechanism that was *computed* can also be *shown*:
`plan["travel_mm"]` is the stroke `animate_object` moves the plunger through
(its `location_mm` takes millimetres directly, so there is nothing to convert),
`plan["stroke_mm"]` is how far into that movement the switch clicks and
therefore the frame `set_material_emission` turns the LED on, and
`plan["latched_cap_gap_mm"]` is where a latching cap comes to rest afterwards.
`render_animation` writes the `.mp4`. Say what it is when you show it: an
illustration of the **intended** motion, not a simulation — nothing there
computes a force or a spring — but drawn from the plan rather than from a guess,
which is the whole reason it is worth showing.

### 7.5 `snap_clip` and `battery_door`

`snap_clip(length, thickness, width, deflection)` returns the arm **and** the
catch it snaps into, sized from the same numbers so they cannot disagree. The
rule it enforces:

```
y_max = K · strain_limit · L² / t
```

`K` is 0.67 for a constant-section arm and 1.09 for one tapered to half
thickness at the tip, because a constant-section beam carries its whole strain
at the root and wastes the rest of its length. A requested deflection over
`y_max` is **clamped**, and the clamp is reported: an over-flexed PLA arm does
not bend less, it snaps at the root. Note that deflection goes with the **square**
of the arm's length, so the cheapest fix for a clip that will not reach is
always to make it longer.

Two things the plan will keep telling you, and both are true:

- **PLA takes 2% strain; PETG takes 3.5%.** For a clip that has to open more
  than once, print it in PETG.
- **Print the arm so the layers run ALONG it, not across it.** A layer boundary
  at the point of maximum strain is a crack that has already started, and a clip
  printed the wrong way round snaps on the first flex whatever the arithmetic
  says.

`battery_door(opening_l, opening_w, style="magnet"|"screw")` returns the plate,
the rebated opening, and the fixings — magnet pockets through
`forge_lib.magnet_pocket` (so `available_depth` still refuses to punch through
the back) or screw bosses through `forge_lib.screw_boss`. The **lip is clamped
up** to whatever the fixing actually needs: a 6 mm magnet wants a minimum wall
of plastic each side of it, an M3 pan head is 6 mm across. Getting that number
wrong is why so many printed battery doors are held on with tape.

### 7.6 The circuit: `circuit_plan` and `wiring_steps`

A printed housing with a perfect switch pocket is still not a lamp.

```python
circuit = maker_lib.circuit_plan(led="led_5mm", color="white",
                                 cell="cr2032_cell",
                                 switch="tactile_6x6_latching")
steps   = maker_lib.wiring_steps(circuit)     # beginner sentences, in order
bom     = maker_lib.bill_of_materials(circuit)
svg     = maker_lib.diagram_svg(circuit)      # one loop, boxes and lines
```

The maths is Ohm's law across the part of the supply the LED does not use:

```
headroom = supply_voltage − LED_forward_voltage
R        = headroom / target_current       → rounded UP to the next E12 value
```

Rounding **up** is the safe direction: too big only dims the LED. Three verdicts,
and the plan names which one you got:

- **`"no resistor needed"`** — the headroom is zero or negative. A white or blue
  LED (3.0 V) on a CR2032 (3.0 V) leaves nothing for a resistor to drop. The
  cell's own ~30 Ω internal resistance is the current limit, and that is a real
  limit, not a hope: it is why an LED taped to a coin cell glows for a day
  instead of exploding. **This is the Litwick default**, and the plan works out
  the actual current from the cell's *fresh* 3.2 V — about 6.7 mA, falling as the
  cell sags, for roughly 23 hours of light.
- **`"resistor optional"`** — small headroom on a supply that limits itself.
  A red LED on the same cell: 56 Ω at 20 mA, and it will work without one. The
  plan states the trade — brighter at first, flat far sooner, brightness visibly
  falling as the cell ages.
- **`"resistor required"`** — everything else. Two CR2032s and a red LED at
  20 mA: (6.0 − 2.0) / 0.020 = 200 Ω → **220 Ω**. Swap the coin cell for
  `aaa_pair_box` and a case that was optional becomes required, because two AAAs
  will happily deliver the 20 mA that kills the LED.

The plan also carries `resistor_gentle_ohms` — the same maths at the *cell's*
recommended current rather than the LED's nominal 20 mA — with the run time for
both, because the textbook answer and the answer that still works tomorrow are
often not the same number.

`wiring_steps` writes it out in `casting.py`'s voice, in the order the mistakes
happen in: polarity first (longer leg is +, the flat on the rim marks the
cathode), the switch's terminal pairs found with a meter before anything is
soldered, one series loop with no branches, every joint insulated, and — the step
that saves the most rework — **test it on the bench before a single drop of
glue**.

### 7.7 The sample

`service/samples/push_lamp_core.py` is the reference: press the flame, the light
comes on; press again, it goes off. Four pieces, four print orientations, one
`part` selector, and a `notes()` helper so the same file that builds the geometry
also answers "what do I solder, and in what order?".

It is worth reading for three failures it was rewritten to fix, all of which
generalise:

1. **Every cutter overshoots the face it enters.** A groove whose top face is
   exactly the floor plane is a coplanar-face boolean, and OCC leaves a 0.04 mm
   sliver there that reads as both a thin wall and a 54 mm² flat ceiling.
2. **A `soft_body`'s cavity floor is not flat.** The inward offset turns the
   corner near the wall, so the floor rises as it goes out. A groove cut to the
   *nominal* floor depth leaves a skin over its far end.
3. **Cut into the shell, then stand things on it.** Subtracting a groove after
   unioning three features onto the floor leaves slivers along every seam.

And for one design decision that is not obvious until you try it: **the housing
has to be two pieces.** The switch has to face the plunger and the plunger has to
come out of the top, so in a single closed body one of the two — the guide's
mouth or the switch's seat — always ends up facing away from the bed. Split it at
the mouth and the switch's seat faces up out of the open base while the guide
stands up off a flat lid. Real enclosures are two pieces for exactly this reason.

### 7.8 Checklist for a maker part

- [ ] Real components chosen **first**, from `maker_lib.catalog()`.
- [ ] Every `verify_against_your_part` sentence passed on to the user — and the
      coin-cell holder's especially, because that one is not boilerplate.
- [ ] No hand-typed clearance anywhere. Every fit came from a `*_plan()`.
- [ ] The body's size **derived** from the parts, with a refusal (naming the
      number) when they do not fit.
- [ ] Every mechanism's plan reported: travel, end stop, guide engagement,
      retention, and what returns it.
- [ ] Any clamp the plan lists repeated in the reply, not swallowed.
- [ ] `circuit_plan` run and its verdict stated, including "no resistor needed"
      when that is the answer — and the trade when it is "optional".
- [ ] `wiring_steps` handed over with the test-before-glue step intact.
- [ ] Assembly order stated: plunger in from the inside, circuit tested on the
      bench, cap on last.
- [ ] Each piece `/check`ed separately, in its own print orientation.

<!-- END maker section -->
