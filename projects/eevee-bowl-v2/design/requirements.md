# Eevee-inspired dog bowl holder — requirements (signed off)

Sheet: `design/refs/design-sheet.webp`. Retry of `eevee-bowl-holder`, this
time built to the sheet's own numbers, with every clearance decided up
front rather than guessed during modelling.

## What it is

A printed holder shaped like a curled-up Eevee: a base with a fur-collar
ring, two ears that slot into the base's sides, a tail that mounts on the
back, and a seat for a removable stainless bowl insert. Only the printed
parts are made here — the bowl and the optional rubber feet are bought.

## Overall size (from the sheet)

| Measurement | Sheet value | mm |
|---|---|---|
| Full width, ear tip to ear tip | 7.5 in | 190.5 mm |
| Full height, base to ear tip | 4.1 in | 104.1 mm |
| Base outer diameter | 6.5 in | 165.1 mm |

Assembled footprint fits the 256 mm bed with no segmentation needed.

## Bowl insert (bought)

Removable stainless insert, 5.5 in / 14 cm diameter x 2 in / 5 cm deep
(~700 ml). The base's seat is sized to it plus the seat clearance below.

## Parts and how they attach — every clearance decided

| Part | Attaches | Clearance |
|---|---|---|
| Base | — | wall **3 mm** (five 0.4 mm perimeters, plus margin) |
| Bowl insert seat | drops into the base | **0.5 mm radial** — insert rim to seat bore, all the way round |
| Ears (L + R) | peg into slots on the base's sides | **0.2 mm per side** — slide fit, keyed peg (anti-rotation rib) so an ear can't spin in its socket |
| Tail | mounts on the back | **10 mm dovetail boss** — a flared rail on the base, slides on vertically; resists pulling straight off, no glue |
| Fur collar | separate printed ring, seats around the base | **0.15 mm** — slide fit over the base's neck |
| Non-slip feet | stick on under the base's feet | off-the-shelf stick-on rubber pads — **bought, not printed**; link only, we never auto-purchase |

## Materials

- Printed parts: PLA or PETG — both work; wall and clearances above hold
  for either. (Requirements v1 assumed PETG for damp resistance; both
  remain valid, the geometry doesn't change.)
- Bowl: stainless steel, bought, sized above.
- Feet: rubber, bought, stick-on — link only, never auto-purchased.
- No sealant needed — the printed parts never touch food or water; the
  steel insert does.

## Build plan

Parametric core (Build123d, via the geometry service, `forge_lib` helpers):
base body (bowl seat, ear-slot lugs, tail dovetail boss) — this is the
functional geometry with every keyed clearance above cut into it.

Organic pieces (generated meshes off the sheet's own drawings, same
`forge_lib` ornament machinery — `leaf_collar` for the fur, `silhouette_part`
for the ears and tail — proportions read off the sheet, not a pixel trace):
two ears, the tail, the fur collar.

Each piece gets its own `/check` (watertight, overhangs against a 45°
practical PLA threshold, bed fit) before export. Export is a separate,
later stage — this build stops at checks + a rendered turntable of the
assembly.
