# Sculpt-for-print thickening

## WHEN

An organic/sculpted part (built by `generate_3d` or hand-sculpted) fails
`min_wall` **after** a boolean cut or union — an inset, a bore, a peg root —
was applied to it. The failure clusters near the cut, not evenly over the
part.

## THE RECIPE

1. If a previous attempt already unioned a joint feature at the wrong place
   or angle, **do not union a second, corrected one next to it.** Regenerate
   the sculpted base fresh (`generate_3d` on the same source crop/sheet,
   oriented and scaled the same) so there is exactly one of everything.
2. Apply exactly **one pass** of the cuts, in order: cut the inset →
   union the corrected peg → union the matching fillet skirt (see
   `peg-boss-junctions.md`). One pass, not iterative patching.
3. Remesh fine — **0.3 mm** — to fuse the booleans into one clean manifold
   surface.
4. Decimate only if the fine remesh goes over the **500k-face** budget. Below
   that, skip decimate — an unneeded decimate pass is itself a place for a new
   thin-wall seam to appear.
5. Re-run the check. If a thin-wall failure has **moved** — off the peg
   junction and into the middle of an unrelated cut — that is a distinct,
   real defect, not the same one recurring. Report it as such rather than
   re-applying the peg fix.
6. Before deepening or widening any inset cut, measure the **local shell
   thickness** at that cut, not the part's nominal wall. An inset that is
   shallower than the shell everywhere can still be deeper than the shell in
   one place along its own length.

## WHY

Ear rebuild, one clean pass (`organic-rework-log.md`): both ears came back
**0 boundary, 0 non-manifold**, confirming the clean rebuild eliminated the
old double-peg artifact. But `min_wall` still failed, at a **different**
place: before the rebuild, failures were at 591/2931 and 608/2922 probes;
after, 467/2769 and 464/2750 — and the failure coordinates moved from the peg
root (Z≈1.6) to inside the inset cut's own 45 mm span (Z=40.2 and Z=57.2,
centred on Z=40). The log's own diagnosis: *"the 1.3 mm-deep inset is cut
into a shell that, in real places along its 45 mm length, is not much more
than 1.3 mm thick to begin with — leaving under 0.8 mm of wall behind the
recess."*

Collar (`build-plan.json`): probe failures went from **314 failing** to **3
failing** (of 44) across a rework recorded under the artifacts
`collar-before-coarse.png` / `collar-after-fix.png`, landing at a final
`min_wall` of 0.017 mm. `organic-rework-log.md`'s own summary calls the
collar *"closest of the four to passing; the 3 remaining probes are at the
bore edge where a tuft valley sits close to the neck cut."*

The proven per-ear recipe, quoted directly: *"exactly ONE pass of: cut the
1.3 mm inset, union the single corrected-angle (145.7 deg) peg, union the
matching skirt, then the proven recipe (0.3 mm remesh; both stayed under the
500k-face limit, so no decimate was needed this time)."*

## REJECTED

- **Unioning a corrected peg alongside the wrong one**, instead of removing
  the wrong one first — `addon/forge/tools/bosses.py`'s module docstring
  names this as the actual first failed attempt on this project: *"a peg was
  unioned onto a sculpted ear at the wrong angle, and there was no way to
  take it back... The first attempt unioned a corrected peg alongside the
  wrong one (two pegs, worse walls)."*
- **Assuming a `min_wall` fail at the same feature is the same defect
  recurring.** The ear rebuild proved otherwise: the peg-root fix worked
  completely (0 failures anywhere near Z≈1.6), and what the check still
  reported was a second, previously-masked defect in the inset. Treating it
  as "the fix didn't work" would have led back into the peg geometry instead
  of the inset depth.
- A coarser first pass at the collar's own cut, evidenced by the artifact
  literally named `collar-before-coarse.png` sitting next to
  `collar-after-fix.png` in the build plan, with the probe count collapsing
  from 314 to 3 once it went through a fresh cut and the same fine-remesh
  recipe as the ears — the prose that would explain *why* the coarse pass
  measured worse was not preserved in the current design log (see
  `docs/recipes/INDEX.md`'s could-not-source list).

## SOURCE

- `projects/eevee-bowl-v2/design/organic-rework-log.md` — sections "1. Ears —
  clean rebuild, no double-peg" and "Open items" (inset thin-wall numbers,
  the proven-recipe quote, the collar's near-pass note).
- `projects/eevee-bowl-v2/design/build-plan.json` — collar probe counts
  (314 → 3) and the `collar-before-coarse.png` / `collar-after-fix.png`
  artifact pair.
- `addon/forge/tools/bosses.py` module docstring — the double-peg rejected
  attempt that this recipe's step 1 exists to prevent.
