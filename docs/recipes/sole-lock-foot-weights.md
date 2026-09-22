# Sole-lock: confine foot-sole weights without flattening the toe roll

## WHEN

Skinning a biped/quadruped foot, where the ankle's normal limb weight-blend
(correctly) lends the shin's bones down into the foot across the ankle
articulation — but the sole, the surface that actually contacts the floor,
must not inherit any of that ankle motion, or the standing plane drives
through the floor when the ankle bends.

## THE RECIPE

1. Identify the **sole band**: the flesh below the height the foot chain's
   own bones were *placed* at, measured **locally** — a cross-section slab
   one of the tag's own ring-spacings thick, at each vertex's own station
   along the chain's forward axis (not a single global height).
2. **Locate the foot by the deform chain**, not by a sub-tag split — on a
   figure where the knee/ankle split fails (a doubled-back chain, an
   ankle sitting almost on top of the knee in chain-parameter terms), the
   chain-based rule still finds the foot; a rule gated on the split
   succeeding does not fire on exactly the figure that needs it.
3. **Inside the band, confine to zero above-ankle weight** — the sole may
   only carry the foot chain's own bones (foot, toe), never shin or higher.
4. **Within the chain, use a soft falloff between foot and toe, not a hard
   span test.** A vertex is on both bones near the ball of the foot and
   should keep both; only a vertex a full foot-thickness or more from the
   toe's segment should lose it entirely. The falloff width is not a new
   tunable — it is the same "about one tag-girth" bound the seam-blend rule
   already uses, applied to the foot's own local thickness.
5. The nearest chain bone always keeps a full licence, so no contact vertex
   is ever left with zero weight.

## WHY

The motivating measurement, `rigforge_skin.py`'s `sole_contact_band`
docstring: the vertex that *defines* the standing plane carried **31.1%** of
its weight above the ankle, and that 31.1% accounted for **100%** of the
jump's **2.220 mm** of floor penetration at the crouch.

Four variants, measured on the same fixture, forced application of each:

```
no lock       386 punctures / 2.388% (art. 238 / 1.473%), floor 0.2793
confine only  412 punctures / 2.572% (art. 264 / 1.648%), floor 0.2793
hard ball     438 punctures / 2.762% (art. 290 / 1.829%), floor 0.3400
              ...and the plane vertex's 0.5305/0.4695 becomes toe 1.000
this rule     426 punctures / 2.675% (art. 278 / 1.745%), floor 0.2793
              ...and the plane vertex keeps 0.5305 / 0.4695 exactly
```

Confinement alone (no soft falloff) is *worse* than doing nothing on
punctures (412 vs. 386) because it strips shin weight that had been
averaging the toe's own dive away, driving the standing-plane vertex to
`DEF-foot.R 0.8621 / DEF-toe.R 0.1379` — and a **hard** ball-line span test is
worse again (438 punctures) because it flattens a real blend
(`DEF-toe.R 0.5305 / DEF-foot.R 0.4695`) to `DEF-toe.R 1.000` exactly where
the toe rolls.

## REJECTED

- **Confinement alone** (zero above-ankle weight, no intra-chain falloff) —
  measured worse than no lock at all on punctures (412 vs. 386), because it
  removes weight that was doing real averaging work.
- **A hard ball-line span test** between foot and toe segments — measured
  worst of the four (438 punctures, floor 0.34 mm, up from 0.28 mm), and it
  destroys a real weight blend at the ball of the foot, replacing it with a
  100%-toe vertex.
- **Splitting the foot by sub-tag** (a `Leg.*.foot` slab cut at the ankle) as
  the way to locate the sole — fails outright on a figure whose knee/ankle
  split doesn't resolve, which was measured to be the exact figure whose
  sole was driving through the floor.

## SOURCE

- `addon/forge/tools/rigforge_skin.py` — `sole_contact_band` docstring
  (~lines 1852–1936): the 31.1%/2.220 mm motivating measurement, the
  four-way forced-application comparison table, and the chain-vs-sub-tag
  location argument.
