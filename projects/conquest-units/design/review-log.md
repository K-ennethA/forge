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
