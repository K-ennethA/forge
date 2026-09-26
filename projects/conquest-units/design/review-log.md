# Artist review log — Conquest units

Append-only. Artist verdicts and intent in their own words; these drive
gates and the eventual animation wave.

## 2026-09-24 — survey render sheet, artist answers (verbatim)

> barkling should be colored as a tree so wooden, and it scuttles with its
> small root like legs. the arms are swung for attacks

> petalfang is a flower serpent type, and draggs itself by its tendrils.
> the center is meant to be a snake head-esque, its facing the wrong way in
> your image

> bligghtcap also looks like its facing wrong way, and this one is a fast
> runner that stides and death is a pop

> mycothrall - yes can make more readable its meant to attach on other
> units, and it crawls

> eldroot - should be able to stand and walk with slow lumbering steps

> for now lets not animate moves yet we can come back to that

> duskmaw is a shadowy monster - also facing the wrong way, its thematic is
> meant to be its chest and bottom leg spikes form a mouth when looking at
> it

Readings:
- ANIMATION IS DEFERRED — record gaits now, author nothing until the artist
  reopens it. Gait notes: Barkling scuttles on root-legs, arms swing only
  in attacks; Petalfang drags by tendrils (not slither/cobra); Blightcap is
  a fast strider, death = quick pop (not slow bloat); Mycothrall crawls and
  ATTACHES TO OTHER UNITS (attack design implication); Eldroot stands and
  walks, slow lumbering steps (it does move).
- FACING: three units read "wrong way" on the survey sheet (petalfang,
  blightcap, duskmaw). The survey already found flower_grunt sculpted
  facing +Y against the -Y convention. A facing audit is required per unit:
  establish each sculpt's TRUE front (petalfang's front = the snake-head
  center; duskmaw's front = the view where chest + bottom leg spikes read
  as a mouth), quote current vs correct yaw, and fix in the improved copies
  + flag roster model_yaw_deg values that compensate today.
- PETALFANG's center is a SNAKE HEAD, not a blossom to enlarge — shape and
  color it to read as a head.
- MYCOTHRALL gets lurid/wet readability colors, approved.
- Duskmaw lore: shadowy monster; the chest/leg-spike mouth illusion is the
  identity — verify the game presents that view and the coloring supports it.
- Style question (faceted vs smooth) was not answered; proceeding faceted
  per docs/BLENDER_RIGGING.md and the low-poly art direction, to be
  confirmed on the next render sheet.

## 2026-09-25 - improved render sheet (11 renders, front + threequarter)
> coloring looks good

APPROVED: the look wave palettes and (implicitly, visible in the approved
renders) the faceted style. Crowd budget 3-5k stands as the working
assumption. First artist-accepted look - palette RGBs in improved/*.json
are now reference values.

## 2026-09-25 - Mycothrall correction
> mythocrall is facing the wrong way, that thing is a tail that attaches to
> the spine

Reading: the protrusion the facing audit read as a head/maw (y -3.8) is a
TAIL - the spine-attachment organ. True front is the opposite end; the
audit evidence was misinterpreted, so the improved copy faces backwards AND
the maw color region is painted on the tail. Fix: rotate 180, re-region
(maw/face colors to the true front if a mouth feature exists there; the
tail recolored as the attachment organ - it may keep the glowing threads,
which suit a spine-hook), update the audit JSON, re-check, re-render.

## 2026-09-25 - Eldroot sit/stand request
> for eldroot, it is currently sitting can we make it stand up by animating
> bones or remeshing or something? so it can sit and stand?

Reading: the sculpt is seated/planted; the boss should have BOTH states.
Plan: rebuild the rest pose STANDING (legs extended via rig-guided mesh
work, extra leg bones - knee + foot per leg, which also fixes the 24 mm
foot-pad sink), then author sitting as poses/clips: sitting idle (dormant,
rooted - the boss-intro state), stand_up transition, standing idle
(current creak/sway), sit_down; retune the lumber walk on the standing
rest. Silhouette approval by render sheet before clips.

## 2026-09-25 - Eldroot standing v2 spec (artist, verbatim)
> its taller standing but the two legs in front should be fully extended,
> meaning he should double in height, hes just sitting in the air now, do
> you understand the requirement before spending more time on this

> we may need model changes to make the feet wider as well to sell it better

Spec: standing = front legs at FULL extension, straight load-bearing
columns - total height roughly DOUBLES (seated ~2.7 m -> standing ~5+ m;
the ratio is the point). The 0.45 m lift is REJECTED ("sitting in the
air"). Sitting re-derived as the fold-back pose. MODEL CHANGE approved:
widen the foot pads so the standing stance reads stable. Game-side height
cap (3.4 m boss allowance) will uniformly scale the model; the state RATIO
survives the scale - raising the cap is the artist's game-side call.
Approval point: seated-vs-standing sheet with the height ratio quoted,
before clips are finalized.

## 2026-09-25 - hero survey answers (artist, verbatim)
> mortis is meant to be a necromancer with a floating book of the dead, he
> is carried by the dead and stand on their arms, he is a robed figure, we
> can make updates to make all this more clear.

> geode is facing the correct way he is a sentient diamond rock creature.
> in game they are less zoomed in but we can support higher quality if
> thats what youre asking

> the other blends, lets pull in the other blends as well mosquitopire,
> and all the rest you mentioned

Readings: MORTIS identity locked - robed necromancer, FLOATING book of the
dead, CARRIED BY THE DEAD standing on their arms (the "grave slab" and the
twenty hands ARE the dead carrying him - not defects; the rework makes all
three reads clearer). GEODE facing confirmed -Y; sentient diamond rock
creature (crystal faceting fits). HERO BUDGET approved: higher quality
supported (30-50k hero tier stands). SCOPE EXPANDED: mosquitopire,
Fidough, magmoo, vampito, huntress, supaoctto join the Conquest roster -
survey them like the others.

## 2026-09-25 - six-newcomer verdicts (artist, verbatim)
> mosquitopire is facing the wrong way, lets drop fidough for now(or
> explore completing this model off reference models not adding to
> conquest) magmoo seems like we didn't capture the rest of the model,
> that model was meant to have disjointed blends as its a lava lizard that
> can spread its form, vampito is a worse mosquitopire(but maybe vampito
> is the better name to keep)

> huntress lets drop,

> supaccotto is a sideview and not facing front

Readings: MOSQUITOPIRE facing confirmed wrong (matches the +Y audit;
rotate 180 in the improve wave). FIDOUGH dropped from Conquest; optional
side exploration: complete the model from reference models, outside the
roster. MAGMOO under-captured: it is a LAVA LIZARD that SPREADS ITS FORM -
the model is meant to have disjointed pieces; re-inspect the blend for
hidden/unrendered objects and collections the survey missed. VAMPITO:
model superseded by mosquitopire, NAME likely kept - one unit going
forward (mosquitopire sculpt, vampito name, pending the artist settling
the name). HUNTRESS dropped. SUPAOCTTO: the shown render was a side view,
not its front - true front is the mask/goggles side (consistent with the
+Y audit; rotate 180).

## 2026-09-25 - Eldroot v2 sheet verdict (artist, verbatim + annotated images)
> can we look at this circled portion and make it look more like this
> second picture
> essentially blend the torso into the legs like a tree skirt armor piece
> and the knees more like this last picture like wooden trunks

Annotated refs saved: design/refs/eldroot-v3-groin-circled.png (the
dangling lower-trunk mass between the legs is REJECTED), eldroot-v3-
skirt-silhouette.png (the drawn W hem: the lower torso flares and blends
OVER the thigh tops like a tree-skirt armor piece / tassets),
eldroot-v3-knee-collars.png (the drawn M zigzags: thigh-shin joins become
jagged bark COLLARS overlapping the piece below, telescoping like stacked
wooden trunks - which turns the rigid-shell seams into intentional design).
Everything else on the v2 sheet stands (2.0x ratio, wide feet, clips).

## 2026-09-25 - full answer set (artist, verbatim)
> magmoo missing pieces were never done, its essentially a serpent no arms
> or legs and should be disjointed lava blobs that can form together to
> create one serpent creature or bounce around separting itself into 3
> parts head portion and upper, main body, and long tail, it can have its
> body on the floor and head and tail in motion

> [vampito name] yes confirmed ... [it] fly/hover

> the cape is tentancles so they should move a bit, they are its tentacles
> that have evolved into a cape so have less movement than its main arm and
> leg tentacles, essentially it functions as a cape, and in between the
> cape tentacles we want them connected by water. he walks upright on two
> legs

> lets finish models before we import into godot, we want to update the
> meshes for the exisiting matching characters

> [duskmaw re-run] yes can re-run as well ... [blightcap] keep bounce for now

Readings: MAGMOO is a fresh build - lava serpent, no limbs, THREE
disjointed blob segments (head+upper / main body / long tail) that join
into one serpent or bounce apart; rest state = body on floor, head and
tail in motion; the existing 34k blob becomes the main body. VAMPITO =
mosquitopire's model + the vampito name, flies/hovers. SUPAOCTTO: the
cape IS evolved tentacles - subtle secondary motion (less than limbs),
WATER membranes connecting the cape tentacles; walks upright on two legs.
SHIP PATH DEFERRED: finish/update all matching characters' meshes first,
then import. Duskmaw pipeline re-run approved. Blightcap keeps the bounce.

## 2026-09-25 - Eldroot v3 verdict (artist, verbatim)
> yes split the skirt front. also can we make the arms and shoulder
> wider/larger it looks small now in comparison to his long legs, we can
> also make the legs a little smaller

APPROVED: tasset plates - the skirt front splits into plates hinged to the
thighs so the sit fold carries them (option b). PROPORTION PASS: shoulders
and arms WIDER/LARGER, legs A LITTLE SMALLER - the doubled legs dwarf the
upper body; rebalance so the standing silhouette reads powerful up top
(standing height may drop below the exact 2.0x; keep the sit/stand ratio
dramatic and quote the new numbers).

## 2026-09-25 - Vineweave intent + scale policy (artist, verbatim)
> vineweave arms are vines should be flexible to move
> we shouldn't consider the game size budget too much we can always scale,
> as long as proportions are fine then its good, its easier to scale down
> then up
> and ok on the rest then

Readings: VINEWEAVE's arms are VINES - not limbs to re-pose into a carry,
but flexible chains: rig them as multi-bone vine chains that MOVE (idle
sway, curl, whip potential); the height fix comes from letting the vines
hang/curl naturally instead of a T-span, which frees the fit box. SCALE
POLICY (applies to ALL Conquest units): author at natural proportions -
proportions are the quality bar, absolute size is not; the game scales at
import, and scaling down beats scaling up. The cell-fit check moves to
EXPORT time (report-only during authoring, enforced at ship). Queue as
proposed is approved: Vineweave -> Mortis -> Duskmaw re-run -> vampito ->
magmoo -> supaoctto.

> geode should be colored like an emerald-ish glowy color

Addendum: GEODE joins the queue - emerald-ish GLOWY coloring (deep green
crystal with emissive glow, fits the sentient-diamond identity + the
planned crystal faceting). Slotted into the hero block: Vineweave ->
Mortis -> Duskmaw re-run -> Geode -> vampito -> magmoo -> supaoctto.

## 2026-09-25 - Eldroot v4 delivered
Tassets: 3 bark plates per side hinged to the thighs - v3's 232-263 mm sit
penetration is 0 in every clip; when SEATED the plates fold up into the
trunk (a 3,276-candidate search found no visible seated placement that
does not stab legs/floor/knuckles), so the seated silhouette IS the
approved original sculpt; plates show riding the thighs during the
transitions. Residual: 13-19 mm plate-on-plate rubbing for a few
transition frames; back-shell rim up to 49.9 mm proud in stand_up.
Proportions: arms x1.49 cross-section, shoulders wider, legs 20% shorter -
ratio now 1.88x. SCALE: the lane auto-refit to the 3.8 m cell (0.895x,
standing 4.56 m) BEFORE the artist's natural-scale policy landed; per
that policy the SHIP build uses --no-cell-refit (standing 5.10 m,
proportions identical - uniform scale changes no pixel of the sheet).
Bake bug caught and fixed: v4 had overwritten v3's texture files; v3
restored byte-for-byte, v4 writes its own names.

## 2026-09-25 - Vineweave + Mortis answers (artist, verbatim)
> vineweave steps and walks like a biped, the forming per attack ideally
> should have the vines merge into a blade and then can un merge into
> vines)not sure how this is done, whether to have a sword mesh that
> morphs from the vines?

> mortis glides - can have ghostly sleeves reaching toward the book, we
> can make the skins as well

Readings: VINEWEAVE walks as a BIPED (stepping on the bark legs, vines
trailing/swinging) - walk clip unblocked. Attack form: vines MERGE into a
blade and UN-MERGE back; implementation design (for the attack wave,
attacks still deferred): animate the vine chains braiding tight while a
faceted blade mesh grows along them (scale/visibility keys), reverse to
unmerge - a swap-under-motion, cleaner for glTF/Godot than morph targets,
though the braid pose itself is bone animation on the existing chains;
blade mesh authored now-or-later with the attack wave. MORTIS: GLIDE
confirmed (the dead carry him smoothly) - glide loop unblocked; ADD
ghostly sleeves reaching toward the book (new geometry, ethereal read);
AUTHOR the two game skins as palettes (Gravemoss, Boneash Sovereign).
