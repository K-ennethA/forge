# Organic rework — final bounded turn: clean ear rebuild, measured seating

Final pass on this project. Earlier sections of this doc (ear inset/
placement derivation, tail proportions, collar generation, triangle-cutout
diagnosis, the seating-rotation algebra) are unchanged and still current.
This section is the clean ear rebuild and the measured (not formula-only)
seating correction.

## 1. Ears — clean rebuild, no double-peg

No pre-peg saved state existed to reload (checked: nothing in the scene or
on disk predates this session's own generations), so both ears were
**regenerated from scratch** via `generate_3d` on the same sheet crop,
oriented, scaled to 70 mm, then exactly ONE pass of: cut the 1.3 mm inset,
union the single corrected-angle (145.7 deg) peg, union the matching
skirt, then the proven recipe (0.3 mm remesh; both stayed under the
500k-face limit, so no decimate was needed this time).

**Result: watertight cleanly on both** (0 boundary, 0 non-manifold —
confirms the clean rebuild actually eliminated the old double-peg
artifact, which was the previous turn's diagnosed cause of a min_wall
regression).

**min_wall still fails, but the failure MOVED — a real, distinct finding:**

| | Before (double-peg) | After (clean rebuild) |
|---|---|---|
| Ear L thinnest / probes | 0 mm / 591 of 2931 | 0 mm / **467 of 2769**, at (-5.6, 2.4, **40.2**) mm |
| Ear R thinnest / probes | 0 mm / 608 of 2922 | 0 mm / **464 of 2750**, at (-2.1, 0.8, **57.2**) mm |

Both failure locations now sit at Z=40 and Z=57 — inside the **inset
cut's own 45 mm span** (centred at Z=40), not at the peg junction (Z~1.6,
where the skirt fix was aimed). The skirt fix worked for what it targeted:
neither failure is anywhere near the peg root any more. What it exposed is
a second, separate defect: the 1.3 mm-deep inset is cut into a shell that,
in real places along its 45 mm length, is not much more than 1.3 mm thick
to begin with — leaving under 0.8 mm of wall behind the recess. This is
not the peg-junction problem re-appearing; it's a different part of the
same ear failing for a different reason, and it was not touched by
anything asked for in this turn (the recipe was the peg junction only).
Left open, not patched — the turn's fix budget went to the rebuild and the
seating measurement below.

## 2. Ear-R seating — measured, not just formula

The formula (`_round_lug_plan`'s own numbers) said the peg tip should land
0.33 mm from the socket axis. The render kept showing a visible gap
anyway. Measured the actual base mesh instead of trusting the formula:

```
Read the base's evaluated mesh (world space), kept every vertex within
9 mm of the formula's predicted socket position (-52.74, -58.58, 76.8) mm.
153 vertices found -- the lug/socket region.

Measured socket centre (X, Y):      (-52.19, -57.97) mm
Vertex Z range captured:             71.29 to 76.80 mm
Bottom-third vs top-third XY drift:  (-49.80,-56.48) -> (-51.90,-58.01)
  -- ~2 mm of horizontal drift top-to-bottom in the captured lug surface;
     some of this is the OUTER lug rib (not the bore wall) mixed into the
     9 mm capture radius, so it is not a clean axis reading, only a bound.

ear-R peg tip, from its ACTUAL object matrix (not hand arithmetic):
  (-52.49, -58.81, 67.97) mm

Horizontal distance, peg tip to measured socket centre: 0.886 mm
```

0.886 mm — inside the 1 mm tolerance, but nearly 3x the formula's
optimistic 0.33 mm. The gap between formula and measurement (~0.55 mm) is
the same order as the swell/`widest` term in `_round_lug_plan` that was
estimated by hand rather than read from the service. **Correction
applied:** both ears' seating X/Y shifted by the measured offset
(ear R to (-52.19,-57.97,76.8); ear L mirrored to
(-52.19,57.97,76.8)), rotation unchanged
(`[34.3, 0, rim_angle]`).

**Post-correction, the render still shows ear-R short of fully flush** (see
`renders/assembly-v2.png`) — 0.886 mm is small at this object's ~10 mm
peg scale but is evidently still enough to read as a visible seam at this
render's zoom. Left as an open, quantified item rather than chased with a
fourth pass: the position is measured and the residual is named (0.886 mm
plus whatever visual gap that magnitude produces at this scale), not
guessed at further.

## 3. Tail — correcting last turn's mis-stated direction

Last revision's table read "0.301 -> 0.115 mm, improved" — **that was
wrong and is corrected here.** A SMALLER min_wall number is a THINNER,
WORSE wall, not a better one. The accurate statement:

```
Before any skirt:        thinnest 0.301 mm, 11 of 3911 probes under floor
After the skirt was added: thinnest 0.115 mm, 18 of 3907 probes under floor
```

This is a **regression**, not an improvement — both the thinnest reading
and the probe count got worse. Confirmed unchanged this turn (not
touched, since this turn's budget went to the ears): thinnest 0.115 mm at
(-84.25, 3.14, 82.3) mm, which is inside the skirt's own footprint
(consistent with the skirt introducing a new thin boolean seam right at
its own edge, the same class of defect the ears showed before their
rebuild). The tail was not rebuilt this turn — that fix (rebuild clean,
skip the skirt or re-derive its geometry) is an open item, not done.

## Final state, all four parts

| Part | bed_fit | min_wall | watertight | overhangs |
|---|---|---|---|---|
| Base | PASS | PASS (1.805 mm) | PASS | WARN (2542.5 mm2, known, documented) |
| Ear L | PASS | FAIL (0 mm, 467/2769 — now at the INSET, not the peg) | **PASS** | WARN |
| Ear R | PASS | FAIL (0 mm, 464/2750 — now at the INSET, not the peg) | **PASS** | WARN |
| Tail | PASS | FAIL (0.115 mm, 18/3907 — regression from the skirt, confirmed) | PASS | WARN |
| Collar | PASS | FAIL (0.017 mm, 3/44 — stable, near-pass) | PASS | WARN |

## Open items, with numbers, for any future turn

1. **Ear inset thin-wall** (467/2769 and 464/2750 probes, thinnest 0 mm,
   centred Z=40-57 mm): the 1.3 mm inset is deeper than the shell in
   places. Fix would be either a shallower inset (e.g. 0.8-1.0 mm) or
   thickening the ear shell generally before cutting it.
2. **Tail skirt regression** (0.301 -> 0.115 mm, 11 -> 18 probes): the
   skirt introduced a new thin seam at its own edge. Fix would be
   rebuilding the tail from its pre-skirt state with a re-derived skirt
   geometry, the same clean-rebuild approach that worked for the ears.
3. **Ear-R residual seating gap** (0.886 mm measured, visible in the
   render): formula and measured geometry agree to within under 1 mm but
   not to render-invisible precision. A true fix needs the base's exact
   swell/`widest` term read from the service rather than estimated by
   hand, or a direct measurement of ear-L's socket too for a fully
   symmetric, doubly-measured placement.
4. **Collar** (0.017 mm, 3/44 probes): closest of the four to passing;
   the 3 remaining probes are at the bore edge where a tuft valley sits
   close to the neck cut.

No further passes taken this turn past the above, per the bounded scope.
