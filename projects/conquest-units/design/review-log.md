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

## 2026-09-25 — Geode movement answer + Godot import queue

Artist: "note down the godot import issues for later" -> created
design/godot-import-notes.md (glow-mask/glTF emission, KHR_animation_pointer,
skin delivery shape, import scale). Ship-path lane starts from that file.

Artist on Geode movement (verbatim): "geode walks and its limbs are connected
statically, maybe have some elecriticty wiring them together, so it walks
pretty normally, can floatish"

Binding reading: limbs do NOT orbit/drift as free parts — they are statically
connected to the body. Optional electricity arcs visually wire the parts
together. Locomotion is a fairly normal walk on the leg crystals with a
float-ish quality (soft contacts, slight body float allowed). Idle must be
revised to match (no independent part drift; keep core pulse).

## 2026-09-25 — Duskmaw v2 feedback (artist verbatim, binding)

"duskmaw - the mouth silhouette is good, we should have an idle animation
where the mouth piece does a chomp

the side of its shadow legs need work, the bottom should almost be uniform
like a shadow coming out of the ground, and its movement should be gliding
as well

this whole model might need some work, lets simply the face(head, the actual
mouth with teeth is a bit lost in this) it doesnt read like a mouth, the
model is meant to be inspired by aku or father from the code name kids next
door, essentially a shadowy figure/monster

it also needs a better recolor, maybe red around its chest/leg mouth piece
to show its a mouth that it devours you with

also the mouth from the back side of it should be closed, like right now you
can see through him but the mouth should close like an actual mouth"

Binding reading: (1) keep the maw silhouette, ADD a chomp beat to idle
(chest jaw + skirt jaw close/open). (2) skirt/leg bottom -> near-uniform
shadow rising out of the ground; locomotion clip becomes a GLIDE. (3)
simplify the head/face - crisp shadow-figure read (Aku / Father from KND
reference: flat dark silhouette, sharp simple features); current teeth-mouth
on the face is lost/muddy. (4) recolor: red framing around the chest+skirt
maw so it reads as the devouring mouth. (5) the maw must be CLOSED from
behind - no seeing through the body; back walls like a real mouth.
Reference identity: shadowy figure/monster (Aku, Samurai Jack; Father,
Codename: Kids Next Door).

## 2026-09-25 — Duskmaw v2 addendum

Artist: "another reference for duskmaw is darkrai inspiration" — add Darkrai
(Pokemon) to the reference set: white smoke/hair plume, single visible cyan
eye vibe, tattered wraith body dissolving at the base, red spike collar.
Reference set is now Aku + Father (KND) + Darkrai: crisp dark silhouette,
minimal sharp face, body trailing into shadow at the ground.

## 2026-09-26 — Magmoo v2 feedback (artist verbatim + original sketches)

Artist shared the ORIGINAL magmoo concept sketches (notebook page "#002
magmoo / lavazard / goo va..."), saved as design/reference/magmoo-sketch-1.jpg
and magmoo-sketch-2.jpg, and ruled:

"magmoo should look more like this, lets make it more of a slime vibe, like
red lava instead of rock with lava in it, more lava blob monster

also can we make the pieces separate and then combine into one long serpent
piece as its idle animation"

Binding reading: (1) identity rework rock -> GOO: smooth glossy red-lava
slime blobs, not basalt crust with magma cracks; the sketch shows a rounded
teardrop head with small simple dot eyes and flame-licks trailing off the
back, a curved smooth mid-blob, small floating droplets between pieces, and
a splashy bottom piece with finger-like splats. (2) IDLE = the split/combine
cycle itself: pieces separate, then combine back into the one long serpent,
looping. Slither walk stays. Resume of paused work also ordered (duskmaw v2
restart, then supaoctto).

## 2026-09-26 — Magmoo v3 feedback (artist verbatim, binding)

"can we have the eyes be within the goo so not the full circles sticking out
more of a negative space with it

also the movement for him shouldnt be a slither he should have the movement
be a flying slither arc moving in a sin/cosine horizontal s shape like a
flying dragon, can we have one of his animations be all the goop goes into
one big ball of goop with his eyes and ears just showing

also the lava coloring should be see transparent see through otherwise looks
fine"

Binding reading: (1) eyes recessed INTO the goo as negative space (glowing
sockets/insets), not eyeball spheres protruding. (2) walk clip -> FLYING
slither: airborne, body arcing in a horizontal sin/cosine S like a flying
(chinese) dragon; rest pose still on the floor per contract. (3) NEW third
clip "ball": all the goop merges into one big ball with only the eyes and
ears (the head's flame licks) showing — ALLOWED_CLIP_NAMES extended with
ball/ball-loop. (4) material becomes translucent/see-through lava; colors
otherwise approved.

## 2026-09-26 — Duskmaw v3 feedback (artist verbatim + annotated v2 renders)

Annotated screenshots saved: design/reference/duskmaw-v3-maw-annotation.png
(red outline drawn around the chest maw shape wanted) and
duskmaw-v3-hem-annotation.png (red zigzag drawn along the base hem).

"for duskmaw lets not have him have a mouth under his eyes and the body
mouth should look more like this (and colored red) with the inside just a
different shade of yellow orange so we can tell its inside

we also lost the shadowy extensions on the bottom like this on he second
picture

the arms look like they got too skinny around the mid part, elbow area

can we also make him a bit tallker and a skinnier neck a little he seems
short and fat now"

Binding reading: (1) NO face mouth under the eyes at all (drop the grin
variant as default candidate; face = eyes only). (2) The body maw follows
the annotation: bigger jagged-toothed opening, RED framing/teeth per the
drawn outline, interior a distinct yellow-orange shade so it reads as the
inside of the mouth. (3) The base hem regained uniformity but LOST the
shadowy extensions — bring back jagged shadow tendrils/flame-like zigzag
around the bottom per the drawn zigzag, layered on the even floor ring
(uniform contact stays, silhouette gets the tendrils). (4) Arms thickened
at the mid/elbow area — currently too skinny. (5) Proportions: a bit
taller, slightly skinnier neck — reads short and fat now.

## 2026-09-26 — Supaoctto v2 feedback (artist verbatim + references)

References saved: design/reference/supaoctto-v2-goggle-annotation.png (the
current head with orange wing accents drawn on the goggle corners) and
supaoctto-v2-visor-reference.png (angular swept W-shaped orange visor -
the superhero glasses shape wanted).

"lets have supacctoo be all blue - the darker one that exists on the
tentacles, it should all be the color,

the color for the water is good but it should connect closer to the bottom
of the full length of the tentacle cape

for the googles lets do an orange on the outer permiter of the google and
the inner a complimenting color

can we also make the goggles look more like this or like the glasses look
so hes more superhero-esque

lets get rid of the mouth piece and just give a small circle hole in its
place

can we also smooth out the textures and align things to be btter placed,
and give the head a thin neck so it connects better to the body"

Binding reading: (1) whole body = the darker cape blue (no coral skin;
one blue all over). (2) water webs keep their color but extend to near the
FULL length of the cape tentacles (currently 62%). (3+4) goggles reshape to
the angular swept W-visor of the reference; orange outer perimeter/frame,
complementing inner (lens) color. (5) mouth piece removed, replaced by a
small circular hole. (6) general polish: smoother surfaces/textures,
better-aligned part placement, and a thin neck so the head connects to the
body instead of floating.

## 2026-09-26 — Supaoctto v2 addendum (artist verbatim)

"chest emblem should be something more appropriate for an octopus superhero
than just a circle, and he walks with confidence like a stride

an animation can have him float up cross his arms, the cape flares a little
bit like if theres wind and then floats back down"

Binding reading: (1) emblem redesigned as an octopus-superhero mark (e.g. a
stylized octopus/tentacle-swirl sigil), not a plain circle. (2) walk gains
confidence - a stride (assertive, purposeful; more than the calm stroll).
(3) NEW clip "float": he floats up, crosses his arms, the cape flares a
little as if in wind, then floats back down - ALLOWED_CLIP_NAMES extended
with float/float-loop.

## 2026-09-26 — Magmoo v4 feedback (artist verbatim + 4 references)

References saved to design/reference/: magmoo-v4-color-reference.png (the
bright glossy orange lava blob with the huge mouth - the target coloring),
magmoo-v4-eye-annotation.png (red circle drawn where the eye belongs -
high on the head side, dragon-like), magmoo-v4-flight-annotation.png (red
S drawn VERTICALLY over the flight render - the wanted up/down undulation;
the mound section is inside the curve), magmoo-v4-current-flight.png.

"this part should only be there when hes idle on the floor otherwise while
flying it should just be full serpent

his eyes should not be white at all or have the black, it just a carved out
eye socked with no eye and the socket is a darker shade of red

when he is in ball form have him do a small bounce

coloring should mathc more this image while remaining a bit see through

eye placement should be more like here like a dragons

and right now hes going in an s flat but we want it up and down like"

Binding reading: (1) the splash MOUND (crown + finger splats) exists only
while grounded (idle); in FLIGHT the body reads as a clean full serpent -
the mound section becomes a smooth body segment (swap-under-motion is the
house pattern). (2) eyes: carved empty sockets only - NO white, NO black
pupil, no glow; socket interior a darker shade of the body red. (3) ball
clip gains a small bounce. (4) colors move to the reference: bright glossy
orange lava (not deep red), still a bit see-through. (5) eyes placed high
on the head sides like a dragon, per the circle annotation. (6) flight
undulation becomes VERTICAL (up/down waves) instead of the flat horizontal
S, per the drawn S.

## 2026-09-26 — Magmoo movement identity (artist verbatim, answers to the
## orchestrator's v4 questions; binding, folds into the v4 lane)

"resting state is segmented on the ground, and he idles between turning
into a ball and the flying s shape

also we should see visible disconnect between the blob sections, it seems
they are all connected right now

i imagine him moving from space to space as goops chasing each other

cartoon slime

he bounces by the flight pattern, so he more launches himself forward with
the goops following him then landing on the next spot as the position
touching the ground

yes he can shed small lava or leave lava

death can be a puddle yes"

Binding reading:
- REST = the segments lying on the ground, visibly separate. IDLE = from
  that rest he cycles between forming the ball and doing the flying-S
  rise, then settles back to segments. One loop can stage both beats.
- VISIBLE DISCONNECT: the blob sections must read clearly separate
  (current build reads connected - widen the gaps; droplets must not
  bridge them into one silhouette).
- LOCOMOTION = bounding leaps, "goops chasing each other": he launches
  forward, the following pieces chase him through the air (the vertical S
  arc is the launch trajectory), then he lands grounded at the next spot.
  The walk clip is that launch->arc->land cycle in place.
- FEEL = CARTOON SLIME: snappy, bouncy - anticipation squash before
  launch, stretch in the air, splat + settle on landing.
- Goo transitions must be FLAWLESS/believable (artist: "comes to life
  from its movements") - stretchy deformation: liquid bridges that neck
  and snap, bulge on contact, relax after. Shape-key work on top of
  bones is approved effort.
- SHEDDING: yes - small lava drips/splats shed in motion or left behind.
- DEATH = collapse into an inert puddle - RECORDED for the deferred
  attack/death wave (not built now); spawn as rising from a puddle noted
  as the natural counterpart.

## 2026-09-26 — Duskmaw v4 feedback (artist verbatim + 2 references)

References saved: design/reference/duskmaw-v4-maw-curve-annotation.png (blue
curves drawn on the v3 maw: the body should CURVE around the mouth, and a
blue jagged line over the top lip = wanted jagged upper edge) and
duskmaw-v4-shadowlord-reference.webp (the target identity art: a long
slender shadow lord - horned head, yellow face, wispy clawed arms, base
dissolving into curling tendrils spreading OUT along the floor).

"duskmaw has overcorrected, his center is now too box like before it was
like the mouth portion was part of his body, now its like we just a box on
him and called it a day

he should still curve like the blue and the red outline on top of the mouth
should be more jagged like shown, the bottom teeth are good

can we also make his torso long and more slender he seems such a box now

his tendrils on the bottom should not be spiked up, lets have them more
like tendrils reaching out on the floor

more like this reference photo and more slender like shown there while
still incorporating the mouth, we can also make the arms more like the
reference"

Binding reading: (1) kill the belly box - the maw is carved INTO a curving
body (blue curve lines), mouth reads as part of the torso. (2) upper lip
line more jagged per the blue zigzag; BOTTOM TEETH ARE GOOD - keep them.
(3) torso LONG and SLENDER (the reference's proportions). (4) base
tendrils are NOT up-spikes: curling tendrils reaching OUT along the floor
(reference's octopus-curl base). (5) arms toward the reference: wispy,
tapering, clawed, with trailing flame-like edges. Overall identity =
the shadow-lord reference while keeping the devouring body maw.

## 2026-09-26 — Supaoctto v3 feedback (artist verbatim)

"for supacctoo its good only thing is the circle mouth should be more of
smirk line when open like a confident hero, and there should be nothing
visible when its closed so a smirk opening when open and nothing when
closed"

Binding reading: v2 approved except the mouth. The static circular siphon
dimple goes. The mouth has two states: CLOSED = nothing visible at all
(smooth mantle, no crease, no dimple); OPEN = a confident-hero SMIRK line
opening (asymmetric upturned corner). Animated via shape key/morph so it
exports; closed is the rest state. When it opens is lane-proposed
(natural candidates: the float hold, brief idle beats) - artist to veto.

## 2026-09-26 — Supaoctto v3 addendum (artist verbatim + 2 annotations)

References saved: design/reference/supaoctto-v3-belt-annotation.png (red
belt drawn at the waist: side plates + dipped centre, superhero belt) and
supaoctto-v3-starfish-mask-annotation.png (red star-arm strokes drawn
around the visor: starfish-inspired mask).

"would a belt like this help supacctto design

can we also try a version of the mask more like this, star fish inspire to
give the aquatic hero vibe better"

Orchestrator's design answer (given to artist): yes on the belt - it
anchors the waist on an otherwise uninterrupted blue body and is the
strongest superhero-coding accessory; echo the accents (render gold and
orange versions to pick from). Starfish mask worth trying as a variant
beside the W visor.

Binding scope for the v3 lane (with the smirk mouth): (1) BELT per the
annotation - side plates, dipped centre; render in gold and in orange for
the artist's pick. (2) STARFISH MASK VERSION - star arms radiating around
the eyes per the annotation, rendered as a variant alongside the current
W visor (both kept until the artist picks). (3) smirk mouth per the
earlier v3 entry.

## 2026-09-26 — NEW UNIT: Firefly jet creature (artist reference sheet)

Reference saved: design/reference/firefly-character-sheet.webp — "FIREFLY
JET CREATURE" three-view sheet: firefighter-style mask with bug-eye
goggles + simple straps/helmet, two feather antennae, jet-pack torso
(rocket/bug fusion, banded abdomen, side thrusters), translucent smoky
vapor wings with ragged edges, bottom flame-propulsion exhaust with a
consistent clean flame shape.

Artist (verbatim): "once thats done begin work on this firefly / the colors
instead of that exact yellow should be more of a firefly glow type color /
and the smokey wings should be coming out of the holes on its side"

Binding reading: build a NEW unit "firefly" from the sheet (no source
sculpt exists - first fully from-scratch build; magmoo's SDF kit is the
prior art). Two deviations from the sheet: (1) the amber/yellow becomes a
FIREFLY-GLOW color (bioluminescent green-tinged yellow) through the glow
tiers; (2) the smoky wings emerge FROM the side-thruster holes, not from
the back. Queued after supaoctto v3.

## 2026-09-26 — Cross-unit feedback round (artist verbatim)

Reference saved: design/reference/magmoo-v5-rest-pose-reference.png (the
wanted default pose: head arched up in the air, droplets hanging, mound +
tail grounded - "paused in motion").

"for magmoo this is a lot better, can we make him floatier while jumping
also default position should be more like this right now all his pieces are
on the floor but we want some still flying as if he was paused in motion

Also we had said only carved out eye, but lets actually put a red goo eye
no pupil just one solid piece where its carved out

and make him more red overall than the solid orange he has right now, the
accent colors are fine

duskmaw is much better and fine for now

for supactto lets keep the non star-fish design for the goggles but instead
of the octopus on the chest lets put the starfish there

possibly another version of the starfish mask is to not have it glued to
his skin meaning it comes off towards the tips and doesn't connect with his
face

firefly is near perfect, my only note is too make the wings smokier like
heavier smoke but its almost perfect"

Binding readings:
- MAGMOO v5: (1) floatier leap - more hang time, softer gravity feel.
  (2) REST POSE per the reference image: head/neck arched up off the
  floor with droplets hanging, mound + tail grounded - paused mid-motion,
  not all pieces flat on the floor. Idle beats (ball, S-rise) restage
  from that pose. (3) eyes = solid RED GOO eye filling the carved socket,
  no pupil, one piece. (4) body MORE RED than the current orange; accents
  stay.
- DUSKMAW: v4 APPROVED - "much better and fine for now". Parked.
- SUPAOCTTO v4: W visor stays (starfish mask NOT the default); the chest
  emblem becomes the STARFISH (replacing the octopus sigil); plus try a
  starfish-mask variant where the star arms detach toward the tips -
  not glued to the face, tips floating off the skin.
- FIREFLY: near perfect; wings get HEAVIER smoke (denser/smokier read) -
  orchestrator handles as a direct tweak.

## 2026-09-26 — Magmoo eye defect + NEW UNIT: Vampire Warrior

MAGMOO EYE DEFECT (artist verbatim): "the eye for magmoo is titled wrong
and sits an angle from the socket" + "the eye should be in the green area"
(annotation: a green loop drawn around the socket region - the orb
currently sits LOW and tilted, with the dark socket hole visible ABOVE it;
the eye should fill the circled area, covering the opening, aligned to the
socket, no hole peeking out). Defect goes back to the v5 lane.

NEW UNIT (artist: "also start work on next character"): VAMPIRE WARRIOR,
from a three-view character sheet (front / back / side + sword detail;
image supplied inline - not on disk, content transcribed):
- Tall female vampire warrior, INTIMIDATING PRESENCE (sheet's own note).
- PALE SKIN, RED EYES, FANGS, very LONG bone-white hair (waist length,
  parted center, drawn over the shoulders in front / full cascade behind).
- Outfit: black high-collar armored bodice with silver seam trim + a red
  gem at the throat; long TORN black cape/coat with dark red lining,
  ragged hem to the ankles; long black gloves; black thigh-high
  high-heeled boots with armored knees; bare upper thighs between boot
  and skirt fauld.
- SWORD nearly her own height, held point-down: LEAF/FLAME-SHAPED BLADE
  (bone-pale, serrated leaf edges like a giant feather/leaf, dark
  centre vein), SIMPLE HANDLE, red TASSEL at the guard.
- Colour palette chips: bone-white, pale grey, near-black, dark red,
  deep maroon, black.
Sheet deviations: none given - build to the sheet.

## 2026-09-26 — NEW UNIT: Fire Sprite / Demon (sheet + artist sketch)

References saved: design/reference/firesprite-character-sheet.webp (the
three-view sheet: FIRE SPRITE / DEMON - headpiece solid ornamental dark
charcoal crown with two horns and a diamond boss, flame rising through the
top; FIRE WAND, gnarled angular design, grants control of flames; BODY =
"pure hellfire, fluid / flowing, NO SOLID FORM"; glowing eye holes; action
examples IDLE / CASTING / JUMP / FLOAT; palette charcoal greys + two
oranges + dark red) and firesprite-sketch.webp (the artist's own notebook
drawing: square one-piece head+torso block wearing the crown, jagged
hole eyes + zigzag hole mouth, ragged flame arms and two flame legs,
flame through the crown).

Artist (verbatim): "another example only comment is to make the body more
square shape so the head and torso blend as one piece like this other
reference"

Binding reading: build "firesprite" from the sheet with ONE deviation -
the body is a SQUARE one-piece block: head and torso blend as a single
squarish hellfire mass (per the sketch), the crown sitting on its top
edge, eyes + zigzag mouth as glowing holes in the block face, ragged
flame limbs. Wand on its own bone (game may hide/swap). Movement wave
needs artist answers (the sheet shows idle/casting/jump/float poses;
casting is attack-wave, deferred).

## 2026-09-26 — Firesprite v1.1 feedback (artist verbatim)

"for fire sprite lets get rid of the mask and just have his face be the
same as the body texture/color wise
also skinnier and pointier legs"

"movement is floaty steps"

Binding reading: (1) FACE_SHADOW off - no dark ember mask; the face is
plain body fire, holes carry the read alone. (2) legs skinnier + pointier
(root radius 0.29 -> 0.21, tip 0.05 -> 0.015, full sharp; toes slimmed).
Both applied as direct orchestrator edits. (3) MOVEMENT = FLOATY STEPS:
he steps like a walker but light - hang time between steps, soft
contacts, drifting; grounded rest implied. The animation lane builds the
real idle + floaty-step walk on this; flame-flow wildness still open
(lane proposes).

## 2026-09-26 — Firesprite v1.2 feedback (artist verbatim)

"can we make the torso blend better with the legs so it doesnt look like a
rectangle on top of legs, make the arms a little bit shorter now and a
little bit fatter otherwise good"

Binding reading: (1) the block hem flows into the hips/legs as one fire
mass (raise the leg smooth-union / flare the lower band) - no rectangle-
on-legs read. (2) arms: shorter than v1.1's full drop, and fatter
(roots ~0.19, tips ~0.09), still elbowless smooth sweeps. Folded into
the running movement lane (it owns the files and rebuilds anyway).

## 2026-09-26 — Firesprite v1.2 addendum (artist annotation, transcribed)

Artist sent an annotated v1.1 front render (inline image, not on disk):
red strokes drawn as long thin vertical strands flowing from the lower
torso/hem down through the legs to the floor, plus jagged strands over
the wand-side arm. Reading (forwarded to the running movement lane, artist
may correct): the fire streams DOWNWARD in thin wispy strands - trailing
flame strands from the hem between/alongside the legs reaching toward the
floor, and a few loose strands trailing off the arms; the torso-leg blend
is this downward flow, not just a wider fillet.

## 2026-09-26 — Firesprite annotation CORRECTED (artist verbatim)

"the red strokes was showing the outline of how wide and long the arms
should be nothing else"

The previous entry's "downward fire strands" reading is WITHDRAWN. The
annotation is the ARM OUTLINE: arms broad and long, shoulders down to
about shin level, elbowless sweeps. The torso-leg blend stays the plain
geometric blend from the v1.2 entry. Correction forwarded to the running
movement lane before it built the strands.

## 2026-09-26 — Vampwarrior v2 feedback (artist verbatim)

"the lipstick does not line up with the mouth and needs to be moved up

the hair is wrong, otherwise looks fine aside from the stiffness we see in
the biped models

also the the black around the eyes is too thick and should be less to have
a thinner outline around the eye

is the clothes does not 1 to 1 match the design but it looks ok

the one thing I will say is can we do an edit or effects to make the model
look cell shaded or have a more comic book style/animated art feeling so it
matches the rest of the art weve done?"

Binding reading: (1) lipstick region shifted UP to sit on the mouth. (2)
HAIR REWORK - "wrong" (unspecified; rebuild toward the sheet reading and
present 2-3 silhouette options; orchestrator asked the artist what
specifically). (3) eyeliner thinner. (4) clothes accepted as-is (not 1:1
but ok). (5) CEL-SHADE the model: comic-book/animated read to match the
rest of the roster - asset-side treatment (quantized/banded tones in the
vertex shading, flattened realism from the bakes, inverted-hull outline
mesh which exports to glTF) + game-side toon shader recorded in
godot-import-notes as the complementary path. STANDING NOTE: "the
stiffness we see in the biped models" - biped clips need secondary motion
(hair/cape follow-through, softer easing); applies to vampwarrior and
future bipeds.

## 2026-09-26 — QUEUED (artist: "for later - dont work right now")

Artist verbatim:

"the cel shading didnt work undo it, at least the black outline around the
character feels off, lets keep the base, the eyeliner is now vertical it
should outline the eyes themselves

the vampire teeth seem to be over the lips instead of coming from insider/
under them

how do we get more of an animated and stylized look like the drawing
reference

for the fire spirit the walking animation should have its legs remains
stiff more cartoon movement than realistic and faster like a little
creature hopping around"

Binding readings for the NEXT working session (nothing dispatched now):
- VAMPWARRIOR v3: UNDO the cel treatment - at minimum remove the outline
  shells ("black outline feels off"); keep the base model ("lets keep the
  base": the v2 mesh/face fixes/follow-through stay). The AO tone-band
  flattening is part of "the cel shading" - revert toward the v1 shaded
  look unless the artist splits that hair later. EYELINER: the v2 LINER_W
  rule produced a VERTICAL line - it must trace/outline the eye contour
  itself. FANGS: currently sit on top of the lips - they must emerge from
  inside/under the upper lip.
- STYLIZATION QUESTION (orchestrator answered in chat, direction to be
  picked): the animated look comes from drawn shadow shapes + the
  game-side toon ramp, not auto-banded AO - candidate levers listed in
  the chat reply; artist to choose before a v3 styling attempt.
- FIRESPRITE walk v1.3: legs stay STIFF (no realistic knee articulation),
  more cartoon, FASTER - a little creature hopping around (cadence up,
  hop read over stride).

## 2026-09-26 — Vampwarrior v3 spec expanded (artist verbatim); RESUME

"can we update it to make it a bit more stylized and fix the other issues,
the hair still needs work on the front portion it doesnt match the drawing
we want long strans in front even if there isnt mainy individual strans and
its a solid piece, give a sharper chin for that stylized look as well and
lower the red thickness and heaviness it should be a fainter red

also the character needs some eyebrows that were shown in the drawing,
maybe if we can do armor to match the clothes in the drawing that might
help as well

otherwise resume work with what i had said"

Binding reading (joins the queued v3 items - cel/outline undo keeping the
base, contour eyeliner, fangs from under the lip):
- STYLIZED PASS via the drawn approach from the chat discussion: authored
  shadow-shape regions + face/proportion dials toward the sheet (the
  game-side toon ramp stays an import-wave item).
- HAIR front: LONG STRANDS falling in front (over the shoulders to the
  chest per the sheet) - may be solid pieces, few strands is fine; it is
  the length/read that matters, not strand count.
- SHARPER CHIN (stylized face read).
- RED fainter: the heavy red paint band thinned + lightened to a faint
  red (reads as the lip paint; lane renders it faint and flags if the
  artist meant the eye red instead).
- EYEBROWS added (the sheet shows drawn angled brows).
- ARMOR detailing pushed toward the sheet's outfit (plate read on the
  bodice/shoulders where the sheet shows it) - "might help" = lane
  judgment, rendered for the artist.

## 2026-09-26 — Vampwarrior v4 feedback (artist verbatim + 3 references)

References saved to design/reference/: vampwarrior-v4-hair-annotation.png
(red strokes over the v3 face: swooping hair strands from the centre part
framing the face in front - the current front pieces read as a hood, not
hair), vampwarrior-v4-face-reference.png (the anime face target: warm
pale grey-mauve skin, THICK expressive brows, red eyes, soft face-framing
strands, parted mouth with small fangs), vampwarrior-v4-fang-annotation.png
(orange circle on a fang ROOT visible at the lip edge).

"regular red eyes is fine but she needs thicker eyebrows, look at the hair
its not strands up front showing the hair, also the skin color is a bit too
much like the grey default blender color

It should be more like the reference

we also shouldnt be able to see this orange highlighted part of the fangs"

Binding reading: (1) eyes stay as the v3 build (regular red). (2) BROWS
THICKER per the reference. (3) FRONT HAIR reworked to read as HAIR
STRANDS framing the face per the red annotation - swooping from the part
down beside the brows/cheeks, like the reference; the v3 front pieces
read as a helmet/hood. (4) SKIN warmer per the reference - the current
tone reads as Blender default grey; shift to the reference's pale warm
grey-mauve. (5) FANGS: the circled fang ROOT visible at the lip line must
be fully hidden - nothing shows above/at the lip edge.

## 2026-09-26 — Vampwarrior v4.1 feedback (artist verbatim)

"there is too many hair strands now

it should be one large one up front and the rest normal hair (the hair is
too tall now on the head)

lets make the red eyes that darker less bright tone afterwall"

Binding reading: (1) the 5-strand curtain per side becomes ONE LARGE
front strand per side; the rest of the hair reads as normal hair mass.
(2) the hair sits too TALL on the head - reduce the cap/lobe volume so it
hugs the scalp. (3) eyes switch to the previously rendered darker/less
bright red alternative (the v3alt_fainteyes tone).

## 2026-09-26 — Vampwarrior v4.2 nitpick + firefly movement + firesprite OK

Reference saved: design/reference/vampwarrior-v42-hairline-annotation.png
(orange line drawn across the forehead where the front strand's top edge
should sit; green line above it = where the scalp hairline goes).

Artist (verbatim): "If I am nitpicking now the strand should go to this
red line, and then hair scalp goes to the green does that make sense?

firefly should float/hover

fire sprite is fine"

Binding readings: (1) VAMPWARRIOR v4.2: the front strand's upper edge
starts at the ORANGE line (lower on the forehead than now); the scalp
cap's hairline sits at the GREEN line above it - so scalp hair shows
between green and orange, and the strand hangs from the orange line down.
(2) FIREFLY movement identity = FLOAT/HOVER: it hovers to rest and drifts
to move (no perching gait); flame stays on; wings read as drifting/
billowing vapor. Movement wave unblocked. (3) FIRESPRITE v1.3 hop:
APPROVED as-is - parked.

## 2026-09-28 — NEW UNIT: Wren (artist sheet; apply the humanoid lessons)

Reference saved: design/reference/wren-character-sheet.webp — "WREN,
OAKVALE VILLAGE / AGE: 16 / ORIGIN: OAKVALE (VILLAGE FARM BOY) / ROLE:
HERO". Three views + detail panels: messy brown hair with a front fringe,
brown eyes, anime face; green PATCHED hooded cloak (worn open, ragged hem,
stitched patches, round brass clasp - cloak + cloth-patch detail panels);
cream shirt with rolled sleeves; dark blue-teal vest; rope sash/belt with
brown ties + belt pouch; baggy brown trousers into white cross-laced sock
wraps; brown buckled boots; TEAL CRYSTAL pendant necklace (detail panel);
leather BRACER on the right forearm with a teal crystal diamond (detail
panel); STAFF = a wooden PITCHFORK, brass ferrule, wrapped grip (detail
panel). Palette chips: brown, cream, olive, sage, teal, gold. Windmill in
the backdrop (village flavor only).

Artist (verbatim): "lets use what we learned and improve our generation
for this new character"

Binding reading: build "wren" applying the vampwarrior lessons from the
START (first pass should land where her v4.2 did): MPFB2 base tuned
STYLIZED from the outset (anime face read - the sheet's), warm sampled
skin (never neutral grey), bold brows, hair as shaped masses with a
proper feathered hairline + fringe strands (no hood read; hair on its own
UV strip so bakes stay clean), painted tight garments + solid loose
pieces, authored subtle shadow shapes, NO cel/outline treatment (learned:
undone; game-side toon stays the import-note path), clips with follow-
through + zero toe dips from the start, pitchfork on its own bone with
the grip transform recorded. Age 16: younger slimmer proportions than
the vampire, boyish earnest bearing.

## 2026-09-28 — Wren v2 face feedback (artist verbatim)

"for next time, we need to spend some time on his face
bigger eyes and bigger eyebrows
the lips and mouth need a touch up, seems the bottom lip does not match top
lip and its too pinched
and in general some smoothing on the fouce,rest looks fine enough for now"

Binding reading: face-focused round. (1) EYES bigger (beyond the +0.40
dial). (2) BROWS bigger. (3) MOUTH: the bottom lip does not match the top
lip and the mouth reads too PINCHED - reshape so the lips pair naturally
and the compression stops pinching. (4) general face SMOOTHING (facet/
retopo density or targeted smoothing on the face). Everything below the
neck is approved for now.

## 2026-09-29 — Wren v3 feedback (artist verbatim): fire emblem style

"lets keep iterating on the face this is an improvement but the style we
are going for is more fire emblem/anime style, so the eyes still bigger,
and what are best practices and techniques on hair to improve it"

Binding reading + the agreed technique stack (orchestrator answered in
chat): TARGET STYLE = Fire Emblem / anime game read.
- EYES: bigger beyond the exhausted dial -> GEOMETRIC enlargement
  (socket + eyeball scaled together) + the anime eye TREATMENT: large
  iris covering most of the opening, thick dark upper-lash line,
  highlight dot region.
- HAIR, per the best-practice stack: fewer/bolder shell CLUMPS with
  varied widths and S-curve rhythm (2-3 size tiers), flow from a crown
  whorl, dark inner cap under the clumps, SMOOTH-PROXY NORMAL BAKE into
  the hair UV strip (the anime one-volume shading trick, done inside the
  flat-shaded contract like the v2 face), painted shading tiers (darker
  roots/underside, angel-ring band highlight) as palette regions.

## 2026-09-29 — Wren v4 feedback (artist verbatim)

"the eye socket is perfect now, whats wrong is the eyeball is too big --
shrink down the iris to fit the eye socket
and we need to get rid of the eye bags and the effect the eyes smoothing
had on the nose and side

let simplify lips and make them less realistic more standard anime smirk
lines, so thinner lips less noticeable, like we were doing 2d

the hair needs work, do we need shading between layers of hair so it
doesnt look like a dome with lines going through it, also give the hair a
side part rather than just straight down the middle, research hair
technicques for anime style otherwise follow best practices for it"

Binding reading:
- EYES: the x1.30 SOCKET is approved-perfect; the EYEBALL/IRIS is too big
  inside it - shrink the iris (and eyeball if needed) to fit the socket
  proportionally (more visible white, smaller iris disc). Eye bags
  REMOVED entirely (not dialed - gone). The orbit-blend distortion on the
  NOSE and the SIDE of the face gets cleaned up (the scale blend leaked).
- MOUTH: simplify to 2D-anime - thin, barely-there lips; the mouth reads
  as a drawn smirk LINE, not modelled lip volume (drop the paired-volume
  realism; keep the seal).
- HAIR: (1) SHADING BETWEEN LAYERS - painted occlusion/shadow bands where
  clumps overlap (the drawn crevice shadow under each overhanging clump,
  on top of the proxy-normal volume) so it stops reading as a dome with
  lines; (2) SIDE PART instead of the centre part (re-flow the whorl +
  fringe accordingly); (3) follow anime-hair best practices throughout
  (silhouette tips breaking the outline, tier rhythm).

## 2026-09-29 — Wren v5 feedback + FE reference set

Artist (verbatim): "the eyes are better, we still have the eye bags and
the lips are still realistic / can you get an actual fire emblem 3d model
to compare to?"

On the model request: ripped FE meshes are Nintendo assets - not brought
into the pipeline even as reference (IP hygiene for a commercial game).
Instead, official artwork saved as measurable style references in
design/reference/fe-style/: fe-ashe-official-art.png + fe-ashe-portrait
(Three Houses Ashe - boyish village archer, Wren's closest archetype) and
fe-alear-male-art.png (Engage protagonist - the modern FE 3D-model face
style). What the FE face treatment does, for the lane: NO under-eye
geometry at all (skin runs flat from the lower lash line to the cheek -
any crease is at most a faint painted line), and NO modelled lip volume
(the mouth is a drawn line with at most a hint of shading beneath;
philtrum/lip relief effectively zero).

Binding v5 items: (1) EYE BAGS: still visible - remove COMPLETELY: the
under-lid region becomes flat skin (crease 0.61 -> ~0 mm), the remaining
lower-lid line + faint shading go too. (2) LIPS: still realistic - go
fully 2D: kill the 1.8 mm upper-lip proudness and the recess below the
seam; the whole mouth area reads as flat skin + the drawn line, per the
FE references.

## 2026-09-29 — Wren v6 mouth feedback (artist verbatim)

"the lip/mouh area is too large, we should have a faint lip line but we
can see where the lip line is much further away from the mouth opening
and leaves us with so much space in that area it looks off and not like
the pictures"

Binding reading: the drawn lip line and the visible mouth opening (the
seam) read as TWO separate features with blank space between them, and
the overall mouth area spans too large a region of the face. Target per
the FE pictures: ONE compact faint line that IS the mouth - the line
sits exactly on the (single, subtle) opening, the area reads small and
tight, no visible second crease and no dead gap. Likely fixes: unify
line + seam (they diverged after the v5 bridge flattening), and/or the
mouth sits too low / spans too tall a zone - re-place per the FE
portrait proportions (mouth-to-nose distance measured off the Ashe
portrait). Dispatch after the hair-decoupling defect lane lands.

## 2026-09-29 — Wren v6 mouth addendum (artist verbatim)

"and the actual line mouth doesnt fill the area"

Addendum to the v6 reading: the drawn line is also TOO SMALL for the
mouth area - it does not span the mouth region. The v6 fix is therefore
both directions at once: tighten the oversized mouth area AND lengthen/
scale the line so it fills it - the line's width relative to the face
matches the FE portrait proportions (measure the Ashe portrait's mouth
width vs face width and match that ratio).

## 2026-09-29 — 3D anime reference acquired (Alicia Solid) + study notes

Per the artist: "look at 3d references for anime style to better
understand how it should look". Acquired: Alicia Solid VRM (the standard
free anime reference model; license permits use, no credit, commercial-
excluding-corporations; recorded in C:\forge-assets\thirdparty\
MANIFEST.md - STUDY REFERENCE ONLY, nothing ships). Face renders in
design/reference/anime-3d/ (front / threequarter / side / full).

Study findings, applicable to Wren v6+:
- MOUTH: a tiny faint line sitting HIGH - close under the nose, roughly
  one nose-width wide, with NO surrounding features: no philtrum, no
  chin crease, no visible seam apart from the line itself. The line IS
  the whole mouth. (Exactly the artist's v6 point.)
- UNDER-EYE: zero geometry - flat cheek straight to the lower lashline;
  any accent is a faint blush TEXTURE tint on the cheek, not shading.
- EYES: enormous, and the detail is TEXTURE-drawn (iris gradient,
  sparkle highlights, lash fan) on a simple mesh; brows are thin painted
  strokes ABOVE the eye, visible through/over the fringe.
- FACE GEOMETRY: extremely simple and smooth - the mesh carries the
  silhouette only (cheek curve, tiny nose wedge, pointed chin); ALL
  facial features are texture/paint. The nose is a minimal bump with no
  nostril geometry.
- HAIR: chunky braid/lock shells with crisp silhouettes; interior kept
  simple; shading soft and volume-wide (consistent with the proxy-
  normal approach).

## 2026-09-29 — Wren v7 hair feedback (artist verbatim + 2 figure refs)

References saved: design/reference/fe-style/fe-byleth-figure-hair.png
(Byleth figure: long flowing locks, each a clean smooth ribbon with a
sharp tip, clearly separated) and fe-archer-figure-hair.webp (silver-
haired archer figure: crisp layered segments overlapping like feathers,
every lock a smooth shell with a defined edge - the closest match to
Wren's short messy cut).

"the hair can still be improved more segmented and cleared ours look
chopped up"

Binding reading: the locks must read as SEGMENTED, CLEAN ribbons -
each lock one smooth continuous surface with a crisp defined edge and a
sharp tip, overlapping in clear layers like the figures; ours reads
CHOPPED UP (broken/noisy surfaces, ragged edges, unclear lock
boundaries). Likely work: smoother per-lock surfaces (more segments
along each lock's sweep / less cut noise), continuous uninterrupted
S-curves root to tip, cleaner lock-boundary edges, fewer random breaks;
the figure refs are the quality bar. Dispatch after the v6 mouth lane.

## 2026-09-29 — Wren v6.1 mouth feedback (artist verbatim, archer figure)

"I think we need to add back some lip dimension and match this closer
there is a lip coming out the bottom lip just a very simple design
compared to what we previously had

also seems lips are slightly less colored than the base color"

Binding reading (target = the archer figure closeup, fe-archer-figure-
hair.webp, mouth region): (1) add back a SIMPLE lower-lip form - one
soft gentle bump under the mouth line that catches light (a single
smooth volume, nothing like the old realistic paired lips; the upper
stays flat). (2) the lip zone tints slightly LIGHTER/less colored than
the base skin (a subtle pale tint, not a lipstick tone). Bounded
follow-up to the v6 mouth lane; dispatch after the v7 hair lane frees
the files.

## 2026-09-29 — Wren v6.1 addendum (artist verbatim)

"there is a small upper lip as well very small subtle"

Addendum: the upper lip also gets a form - VERY small and subtle, less
pronounced than the lower (per the archer figure: a faint upper-lip
plane above the line, barely catching light). So v6.1 = subtle upper +
soft simple lower, both minimal single volumes, paler-than-skin tint.

## 2026-10-02 — Wren v6.1 resumed + context-economy rules (artist)

Artist (verbatim): "lets continue on improving the mouth, making it a bit
larger with a bit of lip how we had discussed before session limits /
after we need to improve our task handling, clearing context and passing
needed context so we can save tokens"

(1) v6.1 mouth lane resumed with the addition: the line widens from
40 mm toward ~46-50 mm (answers the v6 width question: larger). (2)
Context-economy rules added to docs/lane-conventions.md: per-unit STATE
files (wren_STATE.md is the pilot), ~40-line brief budget pointing at
STATE + the one spec entry, resume-first for deltas, terse <=60-line
handbacks, render economy (changed views + one compare sheet only),
clear-and-point recaps on stale resumes.

## 2026-10-02 — Wren APPROVED; style saved; import wave ordered; NEW UNIT: Elias

Artist (verbatim): "great good enough lets save this style for all our
other conquest characters, hair, eyes, lips and other styling weve done,
and lets begin having an agent import the models weve worked on
coordinate with a conquest agent for the characters that do not have an
exisitng character sheet" + "and start working on the next character"

(1) WREN v6.1 APPROVED - good enough; unit parked current at d1a43af.
(2) HOUSE STYLE codified: design/character-style-guide.md (Wren pipeline
= reference implementation; applies from the first pass on all future
humanoids; shared principles for creatures).
(3) IMPORT WAVE ordered: an import agent brings the built units into
git/Conquest (additive, new branch, never touching source assets),
working through design/godot-import-notes.md (6 items), coordinating
with a Conquest-side agent; that agent also audits the game roster for
characters that have NO sheet and NO forge model, so the artist can
supply designs.

NEW UNIT: PROFESSOR ELIAS (sheet inline-only; transcription binding):
"PROFESSOR ELIAS - ROYAL RESEARCHER. AGE: 60+. ROLE: ROYAL RESEARCHER
(MAGIC / HISTORY / SCIENCE). Wiser, calm, intelligent; royal service;
wears glasses." Three views + detail panels:
- Tousled grey hair, round wire GLASSES, full grey beard + mustache
  (head detail panel: lined kind face, heavy brows).
- Blue cravat/ascot at the throat; navy scholar MANTLE/coat with short
  capelet shoulders, gold trim + gold diamond motifs, ragged-ish hem;
  ACCESSORY detail: a gold trident/crest brooch with a teal gem on the
  chest; the same trident crest appears on the mantle back and the book.
- Cream/parchment under-robe, big rolled sleeves; dark bracers; wide
  brown belt with pouches + SCROLL TUBES tucked in; brown cross-body
  SATCHEL (bag detail: buckled flap, scrolls poking out) worn on the
  back; dark brown trousers; brown gold-trimmed boots.
- STAFF (detail panel): tall wooden staff topped with a brass ARMILLARY
  SPHERE holding a teal orb; brass ferrule.
- BOOK (detail panel): dark tome with the trident crest + clasp (likely
  a prop for casting/idle flavor; own node like all props).
- Palette chips: navy, dark brown, taupe, cream, gold, teal.
Build per the HOUSE STYLE GUIDE from the first pass (elderly dials:
age-appropriate face, the beard as groomed masses, glasses as thin
geometry, hero tier). QUEUED for the fresh session.

## 2026-10-02 — NEW UNIT: General Varden (sheet SAVED, transcription binding)

Sheet file: design/reference/varden/varden_sheet.webp (pixel-sample skin
and palette from it — do not eyeball tones). Transcription (binding):
"GENERAL VARDEN — KINGDOM GENERAL. AGE: 48+. ROLE: KINGDOM GENERAL
(MILITARY / COMMAND). Scarred / battle-hardened; gruff / stubborn;
commands respect." Three views + detail panels:
- HEAD: tousled grey-brown hair swept back, SHORT-CROPPED full beard +
  mustache with grey streaks, heavy brow, lined stern face (48+, not
  elderly — harder/squarer than Elias), weathered/scarred skin.
- CLOAK: huge navy cloak with a dark FUR MANTLE over the shoulders/
  collar, gold trim at hem, gold kingdom-emblem motifs on the skirt,
  the angular gold TRIDENT-CREST EMBLEM large on the back (cloak
  emblem detail panel), round gold BROOCH with radial pattern + chain
  at the collar (cloak/brooch detail panel).
- OUTFIT: high-collar navy tunic with gold-trimmed collar; cream/white
  rolled under-sleeves; dark gloves with gold-trimmed VAMBRACES; white
  tabard panel under the cloak front; wide brown leather BELT + cross-
  body sword strap, heavy buckles and studs (belt/strap detail panel);
  dark trousers; heavy brown buckled BOOTS with gold-edged cuffs.
- SWORD (detail panel): longsword at the left hip — gold cruciform
  crossguard and pommel, dark wrapped grip, dark scabbard with gold
  chape. Own bone + node per props law.
- Palette chips: navy, dark brown, grey-taupe, cream, gold, steel teal.
Build per the HOUSE STYLE GUIDE from the first pass; hero tier; the
fur mantle is a new garment class (fur clumps, not cloth). Elias
pipeline is the nearest reference implementation.

## 2026-10-03 — Wren corrections + run movement intent (artist, verbatim)

(1) "wren is a male not a farmgirl" — WREN IS MALE, the Conquest story
hero. Binding for all docs, briefs, clips and voice work; the approved
v6.1+ model is unchanged.
(2) Run clip intent: "young hero sprint" — eager, youthful full sprint
(not a soldier's measured stride). First locomotion clip beyond
idle/walk; game side selects run by move speed with walk fallback
(hero-anim lane). Walk/idle clips stay as approved.

## 2026-10-03 — Elias SHEET SAVED + scalp hair rejected (artist, verbatim)

Sheet file now at design/reference/elias/elias_sheet.webp (was
inline-only; pixel-sample skin/eye/hair tones from it — kills the
placeholder-tone open question). Artist on v4's scalp: "the hair is
split with long bangs and hair flowing but in our pass its just a
hair bowl, the hair is much closer to wrens but we just have a bowl,
why are we not taking our learnings and improving." BINDING: Elias
scalp v5 = the sheet's actual style — side-split part, long bangs
sweeping across the forehead, flowing tousled side/back masses with
real silhouette variation (Wren-grade lock construction + the
research's mass-first hierarchy: 4-7 primary masses, lock width
spread >= 3:1 — the artist's critique green-lights that direction
for Elias). Beard shell v4 is NOT in question here. PROCESS LESSON
(binding for every unit): hair/garment SILHOUETTE is judged against
the SHEET VIEWS, not the text transcription; every sheet gets saved
as a file and silhouette-matched before a first pass ships.
