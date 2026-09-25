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

## 2026-09-25 - custom protagonist renders (mpfb_custom_*)
> the hair is wrong and is there a way we can make the clothes not skin
> tight so it feels more feel and not like a skin suit

Reading: (a) HAIR rejected - the cap + messy-cards combo does not read as
the reference's dark tousled mop; needs a modeled hair mass (clumped,
directional, low-poly) rather than thin cards; (b) CLOTHES read as a skin
suit because the garments were built by offsetting the body surface, so
they track every body curve. Real garments need their own silhouette:
the jacket hangs from the shoulder line and falls straight past the waist
(boxy, air between leather and torso, open front standing off the chest),
sleeves are cylinders wider than the arm; jeans fall straight from the
hip as fabric tubes with bunching at knee and ankle, not leg-shaped.
Drape/gravity shape + fold geometry, THEN the MPFB fit binding (fitting
does not require tightness). Fit checks must change too: poke-through
stays zero but a MINIMUM CLEARANCE band (except at shoulders/waistband
anchor lines) becomes the new gate so tightness is measured, not eyeballed.

## 2026-09-25 - outfit v2 verdict + gate recalibration (artist, verbatim)
> the outfit had some more issues, the butt area is tight and then goes
> loose and is not fluid, the bicep area of the leather jacket should be
> tight and not loose like the rest of the material since they have
> muscular build and a little tighter on the forearms and wrist but not
> skin tight

> [crouch] thats fine for now ... [gate recalibration] yea sure, i havent
> seen the updated animations

Readings: v3 fit is DIFFERENTIAL - a real jacket on a muscular build:
biceps TIGHT (cloth follows the muscle), forearms/wrists a little tighter
than the body drape but not skin-tight; the torso drape stands. JEANS
seat: the tight-then-loose transition at the butt is not fluid - the seat
needs one continuous fall (fitted at the seat top flowing into the
straight leg, no abrupt ease change). Clearance gate gets PER-ZONE floors
(bicep/forearm zones get lower floors by design, not by failure). Crouch
approved for now. Mocap-norm gate recalibration approved (opposition etc.
re-pinned to real-human values).

## 2026-09-25 - mocap preview verdict (artist, verbatim)
> the model seems broken on all the animations and isn't using the new
> model youve been showing so its hard to tell
> like a weird shoulder makes walking hard to judge
> on running the thigh more slides than moving up
> crouching should have the character bend the knees as well and have the
> arms flared out at an angle

Readings: the previews show the OLD shipped body (Form A conversion) - the
mocap was retargeted onto it because it IS the in-game character; the MPFB
body has not shipped yet. Verdict: judging motion on the old body is
wasted - the mocap set gets retargeted onto the MPFB v3 body next (its
game_engine rig + clean weights) and re-previewed there. Defects to carry
into that pass as measured checks: (a) shoulder deformation = the old
body's known weight defect (elbow/trapezius items from the Form A backlog)
- expected to die with the body swap, verify; (b) RUN: the thigh
translates/slides instead of rotating up - measure hip-joint rotation
amplitude on the retarget and fix mapping/IK if it persists on the new
body; (c) CROUCH: must have real KNEE BEND (verify knee flexion angles
came through the retarget - the artist sees none) and ARMS FLARED OUT at
an angle for balance - an authored adjustment layer on the crouch pair or
a different source style, artist words win over source fidelity here.

## 2026-09-25 - outfit v3 delivered (differential fit)
Per-zone clearance gate: bicep 4.6/6.1 mm (tight, reads the muscle),
forearm 8.9/10.0, wrist 8.5/9.8, torso drape untouched (15.2/46.0), seat
now one smooth C2 fall (ease 10.5->20 mm, worst drawdown 0.8 mm vs v2's
9.7 mm collapse-and-refill). v2 re-scored as the rejected baseline (its
uniform-loose sleeves fail every sleeve zone). Poke-through 0; canary
fires. Untested: the tight sleeves in MOTION - the retarget-onto-MPFB
wave covers it. Pre-existing, out of spec: jeans yoke cleft line,
jagged back-pocket stitches.
