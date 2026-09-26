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

## 2026-09-25 - outfit v3 seat verdict (artist, verbatim + render saved)
> the butt still goes in thats now how butts look like and the area around
> the groin is too tight as well

Ref saved: design/refs/jeans-seat-v3-rejected.webp. Reading: the cloth
still DIPS INWARD between the buttocks - the cleft reads as a valley. Real
jeans bridge the cleft entirely: every horizontal cross-section of the
seat band must be CONVEX across the back (fabric spans cheek apex to cheek
apex like a chord; zero concavity between them) - that becomes a measured
gate, not a clearance number. And the GROIN/front-crotch is too tight -
raise its ease so the front drapes (fabric bridges the front the same
way). v3's per-zone clearances were necessary but not sufficient: cloth
shape is about CONVEXITY, not just distance from skin.

## 2026-09-25 - v3 sleeve addendum (artist, verbatim)
> and the biceps are too skin tight now
> we overcorrected

Reading: bicep target sits BETWEEN v2 (16.5/34.6 - loose) and v3 (4.6/6.1 -
shrink-wrapped): snug enough to read the muscle, with visible cloth body -
aim ~9-13 mm median, leather has thickness and never vacuum-seals. The
zone floors get re-pinned to that band.

> for both biceps and forearms

Addendum: the ease-back applies to biceps AND forearms - both snug with
cloth body, not skin-tight. Forearm target up from v3's 8.9/10.0 toward
~11-15 mm median; wrist may stay near v3.

> you can see it scrunched the armpit torso area as well

Addendum: the ARMPIT/torso junction is scrunched in v3 - the tight-sleeve
blend into the shoulder/armpit anchor band crumples there (a v2-era fold
fix regressed under the new sleeve). v4 must smooth the armpit junction:
the sleeve-to-torso transition is a clean saddle, no pinched folds; add a
local surface-smoothness check (crease/fold detection in the armpit band)
so it cannot silently return.

## 2026-09-25 - outfit v4 delivered
Seat convexity gate: worst cleft dip 38.0 (v2) / 26.8 (v3, the rejected
render) / 0.42 mm (v4, limit 1) - the fabric now spans the cheeks. Groin
bridged (front convexity 41.4 -> 0.97 mm; crotch-V floor 16 mm). Arms
re-pinned mid-band per the overcorrection call: bicep 8.9/10.5, forearm
10.6/12.8. Armpit: 0 sharp valleys (v2 and v3 both measured ~2x curvature
noise - the scrunch predated v3). Poke-through 0, canaries fire, tris
unchanged. Still open: stitch lines blockier where the span moved
vertices (pre-existing spec), sleeves untested in motion (retarget wave).

## 2026-09-25 - outfit v4 verdict (artist, verbatim)
> yes this model is much better and at a good enough spot for now

APPROVED - the protagonist look (body, outfit v4, hair) is locked for now.
This unblocks the retarget wave: the full mocap set (CMU locomotion + the
Mixamo sprint and combat batch) goes onto this body, then the contract
delivery replaces the old character in the game. Also from the artist:
palette-swap skins are approved as a standing feature; and iteration speed
feedback - parameter-level tweaks go DRAFT MODE (direct edit + rebuild,
minutes) instead of full lanes; lanes only for structural changes.

## 2026-09-25 - retarget wave delivered: 16 clips on the approved body
All 16 gated (stretch 0.0%, seams <=0.001 mm). Sprint 7.215 m/s IN-GAME -
inside the 7-9 band, the honest sprint exists. The artist's three notes
answered with numbers: (a) the shoulder was a CLAVICLE RETARGET BUG (rest
direction matched absolutely; now carried relative to rest - walk peaks
7.3 deg vs 38 before), not the body; (b) run thigh ROTATES 70.3/66.6 deg
at the hip, knee to 108 - no sliding; (c) crouch got authored layers:
knees to ~79-83 deg flexion, arms flared 32 deg out per the artist. Also
fixed in shared code: FBX 30fps takes were playing on a 24fps clock;
Mixamo preset verified on 12 real files (Spine mapping dropped - swung
hips 45 mm). Sleeves in motion: 0 visible poke-through after masking
covered layers. Delivery-lane decisions parked: talk take is seated
(upper body used on standing legs), one-shot travel keep-or-strip,
combat stance yaw. Speeds for the manifest: walk 1.785, run 4.362,
sprint 7.215, crouch_walk 0.710.

## 2026-09-25 - clip previews verdict (artist, verbatim)
> on eldroot the dissapearance and reappareance of the skirt is a little
> too much but fine for now - better to keep iterating on others to advance
> for the running hands should be in a soft closed position
> for crouching number four he should have his leggs more spread apart
> for walking number 3 his arms are too close to his side and it makes his
> movement feel too rigid
> small improvements here to make it more lifelike

Readings: ELDROOT accepted for now (tasset fold-away noted as "a little
too much" - candidate future polish: stagger the plate tuck or fade;
advance other units first). CLIP REFINEMENTS (lifelike pass): RUN - hands
in a soft closed position (finger-curl layer); CROUCH_WALK - legs more
spread apart (stance-width layer); WALK - arms carried too close to the
sides, reads rigid (arm-carriage angle out + freer swing).
