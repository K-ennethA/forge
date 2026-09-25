# Artist review log — werewolf / game characters

Append-only. Artist verdicts in their own words; these drive gates.

## 2026-09-24 — protagonist_human vertex-color look (look-dev renders)
> we still never fixed the back, also the clothes blends with the skin in
> multiple parts, its good enough to not block the other agent from
> developing but we need to fix these things

Reading: (a) the BACK geometry defect stands — the torso's back carries
open-jacket front geometry (also flagged by the look lane); it predates the
color pass and is a mesh repair, not a paint fix; (b) the vertex-color
REGION boundaries bleed — skin color appears inside clothing zones (and/or
vice versa) in multiple places; region assignment + the 3-ring blur need
tightening, and region purity should become a measured gate (color-class
audit per tagged zone, allowed mixing only within N rings of a boundary);
(c) shipping verdict: current asset is good enough for the game side to keep
developing — fixes land as a rebuild, no contract change.

Both defects go to the UV/geometry repair + look-fix lane (queued behind the
locomotion wave lane — same file area, so they cannot run concurrently).

## 2026-09-24 — locomotion wave previews (run/sprint/fall-land mp4s)
> the running is clearly broken and the model looks bad for this kind of
> game needs to be much better quality, would an ai agent be better at
> generating this than us using forge

Reading: (a) the run fails on LOOKS despite every mechanical gate passing —
the gate set measures seams/slide/stretch, nothing measures whether motion
reads human; a motion-quality gate is required (defect->gate law); (b) the
model quality bar is reset upward — procedural keyframing and the current
base mesh are below it; (c) strategic answer: pivot to retargeted mocap
(rigforge_retarget exists, takes .bvh) for humanoid motion and an
open-source parametric base mesh (MPFB-class) for humans, keeping forge's
contract/gates/export and reserving generation for likeness, clothing and
custom creatures. Research dispatched (docs/research/mocap-and-base-mesh.md
when it lands). The geometry/look repair lane was STOPPED mid-flight —
repairs wait on the base-mesh decision.

## 2026-09-24 — quality bar addendum
> and low poly and low detail

Reading: the current 16.7k-tri tier is itself below the bar - not only the
motion and coloring. The character target moves to the semi-realistic tier
now (30-50k tris, baked normal/AO detail, real texturing per
docs/research/3d-generation-and-detailing.md) rather than "low-poly first,
semi-real later". The game's own placeholder philosophy still holds for
NON-shipped drafts, but delivered characters aim at the higher tier.
