# Check stage — per-piece results, every clearance quoted

Printer: Elegoo Centauri Carbon, bed 256 x 256 x 256 mm. All four checks are
`bed_fit`, `min_wall`, `overhangs`, `watertight`; overhangs against the
printer profile's 50 deg-from-vertical limit — every remaining unsupported
area here is measured at the steepest possible angle, 90 deg (a flat
ceiling), so it fails a 45 deg PLA threshold exactly as hard as it fails the
profile's 50.

**Final revision: the tail's joint is a round peg now, not a dovetail.**
The dovetail was a planning choice, not the sheet's requirement, and its
flat trapezoid flanks needed more width than the tail's pointed root could
give it — two sizing moves only reached 0.628 mm against the 0.8 mm floor.
Replaced with the SAME mechanism the ears use (`silhouette_part`'s own
`peg=`), sized by measuring the root itself rather than guessing. All four
pieces now clear every hard check.

## How the tail peg diameter was derived

1. **Measured, not estimated.** An intentionally oversized probe peg (25 mm,
   then narrowed once to clear an unrelated thickness check) was built
   against the tail's real outline. forge_lib's own refusal named the
   number: *"the outline is only **8.84 mm** wide at its bottom edge."*
   That is forge_lib's own root-width measurement — the same one it uses
   internally to decide whether a peg fits — not a hand estimate.
2. **Root-width-based ceiling:** 8.84 - 2 x 0.8 mm (the min_wall floor, per
   the artist's instruction) = **7.24 mm**.
3. **Thickness-based ceiling (binding):** the appendage is 9 mm thick, and
   forge_lib's own peg-in-thickness check uses a 1.0 mm margin each side:
   9 - 2 x 1.0 = **7.0 mm**. This is smaller than the root-width ceiling, so
   it is the one that actually limits the diameter.
4. **Chosen: 6.8 mm** — just under the binding 7.0 mm ceiling rather than
   exactly on it (a computed value sitting exactly on a limit is the kind of
   thing floating point turns into a fail by 1e-9).

`tail_peg_length_mm` = 11 mm and `tail_peg_clearance_mm` = 0.2 mm, matching
the ear peg's own length and per-side clearance exactly ("the same joint
type the ears use").

## Base (part 0)

| Check | Result |
|---|---|
| bed_fit | PASS — 170.88 x 170.27 x 76.8 mm, 6/6 orientations fit |
| min_wall | PASS — thinnest 1.767 mm of 566 probes |
| overhangs | WARN — 2532.7 mm2 unsupported (3.0% of area), steepest 90 deg |
| watertight | PASS |

**Clearances cut into the base:**
- Insert seat: **0.5 mm radial** — `fit_clearance`
- Ear sockets (x2): **0.2 mm per side**, slide fit with a keyed anti-rotation rib — `ear_clearance_mm`
- Tail socket: **0.2 mm per side**, same keyed peg/socket mechanism as the ears — `tail_peg_clearance_mm`
- Neck (for the separate collar to slide onto): plain cylinder, clearance lives on the collar's own bore

**The overhang warning, unchanged from earlier revisions and still the
honest state:** each of the three rim lugs (2 ear, 1 tail — now all round,
all built by the same helper) sits centred outside the body's own radius,
which it has to in order to clear the wall and hold its socket. That leaves
the outer part of each lug's flat bottom face hanging over nothing — a 90
deg ceiling. A shelf-ring fix was tried earlier and reverted for making it
worse (2667 -> 17892 mm2) — see the design history in this file's prior
revisions. **Shippable as a warning** — supports under three small, known
areas, nothing structural.

## Ears (part 1, printed twice — L and R are the same geometry)

| Check | Result |
|---|---|
| bed_fit | PASS — 30.06 x 81.06 x 9 mm |
| min_wall | PASS — thinnest 2.1 mm of 6 probes |
| overhangs | WARN as modelled (+Z): 63.3 mm2 (1.4%) — **0 mm2 printing -Y up** |
| watertight | PASS |

Peg: 6 mm diameter, **0.2 mm per side** clearance.

## Tail (part 2) — now PASSES every check

| Check | Result |
|---|---|
| bed_fit | PASS — 29.3 x 88.92 x 9 mm |
| min_wall | **PASS** — thinnest **2.38 mm** of 4 probes (was 0.628 mm and failing, with the dovetail) |
| overhangs | WARN as modelled (+Z): 92.0 mm2 (1.8%) — 43.4 mm2 printing +Y up |
| watertight | PASS |

Peg: **6.8 mm** diameter (derivation above), 11 mm long, **0.2 mm per side**
clearance — the same mechanism, same clearance convention, as the ears.

## Fur collar (part 3, separate piece)

| Check | Result |
|---|---|
| bed_fit | PASS — 197.5 x 198.01 x 36.44 mm |
| min_wall | PASS — thinnest 2.66 mm of 174 probes |
| overhangs | WARN — 464.2 mm2 (0.7% of area), steepest 90 deg |
| watertight | PASS |

**Clearance:** **0.15 mm**, positive (slide fit) — `collar_clearance_mm`.

## Overall: PASSED

All four pieces clear `bed_fit`, `min_wall` and `watertight` outright. All
four carry an `overhangs` WARN — small, specific, well-understood areas
(three rim lugs on the base, the peg undersides on the ears and tail, the
collar's leaf undersides) that print fine with supports and are explicitly
"shippable" per the printability rules. Nothing is failing.
