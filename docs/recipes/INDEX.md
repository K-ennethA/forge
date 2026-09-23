# Recipe index

Hard-won procedures, distilled from run logs and lane records so the next
session doesn't re-learn them at full price. Each recipe is WHEN / THE
RECIPE / WHY (measured numbers, quoted) / REJECTED (what was tried and
measured worse) / SOURCE (file + context).

**Before working a stage, check this index for a matching situation and
follow the recipe over improvisation.** If a run learns a better recipe than
what's written here, say so in the report — that's how this library gets
updated.

## Modelling / printing

| Recipe | Situation |
|---|---|
| [sculpt-for-print-thickening.md](sculpt-for-print-thickening.md) | An organic/sculpted part fails `min_wall` after a boolean cut (inset, bore, peg) is applied — especially when a prior fix attempt already left the part in a bad state. |
| [peg-boss-junctions.md](peg-boss-junctions.md) | Attaching a parametric peg/boss onto a sculpted part by boolean union, especially when the angle needs iterating. |
| [seating.md](seating.md) | A part has to seat precisely onto another part's socket or bore. |
| [sculpted-part-hybrid-rule.md](sculpted-part-hybrid-rule.md) | A design needs both mechanical function and organic character ("a real X that looks like Y"). |
| [organic-path-choice.md](organic-path-choice.md) | The deliverable is a purely organic form (character part, shape from a picture): choose the sculpt lane before writing anything, and save the deliverable the moment it exists. |
| [print-min-wall-triage.md](print-min-wall-triage.md) | `partforge_check` fails and you need the right first move, not a guess. |

## Pipeline / infrastructure

| Recipe | Situation |
|---|---|
| [deterministic-image-extraction.md](deterministic-image-extraction.md) | The artist gave you a picture (a floor plan, or anything whose measurements matter) that has to become geometry. |
| [fixture-freezing.md](fixture-freezing.md) | A test suite needs the same synthetic geometry every run for its thresholds/digests to mean anything. |
| [measure-with-trackers.md](measure-with-trackers.md) | Any spatial adjustment: track the landmarks, move with `grab_to`, verify with `probe`, and render only for appearance. |

## Rigging / skinning

| Recipe | Situation |
|---|---|
| [measure-reach-off-the-rig.md](measure-reach-off-the-rig.md) | You need a limb's real reach (for a stretch budget, a crouch/jump cap). |
| [sole-lock-foot-weights.md](sole-lock-foot-weights.md) | A foot's sole must not inherit ankle-blend weight, but a hard cutoff between foot and toe reads wrong at the roll. |
| [cross-part-bridge-detection.md](cross-part-bridge-detection.md) | Verifying a tagged mesh for silent welds between body parts before skinning. |

## Evidence and honesty

Every number in every recipe above is a direct quote from the source file
named in that recipe's SOURCE section, with the file and the surrounding
context cited. Nothing here is an invented step.

## Could not source honestly

The lane brief that produced this library named a specific "collar recipe":
*"fresh bore recut then fine 0.3mm remesh then decimate, coarse-first
measured worse."* This could only be **partially** sourced.

What's real and quoted: `projects/eevee-bowl-v2/design/organic-rework-log.md`
documents this exact recipe — cut, union peg, union skirt, 0.3 mm remesh,
decimate only over 500k faces — for the **ear** rebuild, and
`projects/eevee-bowl-v2/design/build-plan.json` records the **collar's** own
probe-count evidence (314 failing → 3 failing) alongside two artifacts
literally named `collar-before-coarse.png` and `collar-after-fix.png`.

What could not be found: `organic-rework-log.md` itself opens by saying its
earlier sections — including "collar generation" — are "unchanged and still
current," but those earlier sections are **not present** in the file as it
exists now (this is not a git repository, so there is no history to recover
them from). The specific prose that would explain *why* a coarse-first pass
on the collar measured worse than the recut-then-fine-remesh approach was
lost before this pass could read it. `sculpt-for-print-thickening.md` uses
the ear's fully-sourced version of the recipe and the collar's surviving
numbers, and says exactly this in its own REJECTED section rather than
inventing the missing causal explanation.

No other requested recipe had to be dropped or partially sourced.
