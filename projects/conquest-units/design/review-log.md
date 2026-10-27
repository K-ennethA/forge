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

## 2026-10-03 — Elias v5 hair APPROVED; beard same treatment; Wren drops pitchfork

Artist (verbatim): "hair looks good on elias, maybe the beard can get
the same treatment and can we drop the pitchfork from wren."
(1) Elias v5 scalp APPROVED.
(2) Elias beard v6: the SAME treatment as the scalp — mass-first,
sheet-silhouette-matched construction (the sheet's beard is flowing
clumped strands with a soft rounded taper; judge against the sheet
views incl. the head detail panel), replacing/overlaying the v4
smooth lobed shell wherever the sheet disagrees with it.
(3) WREN: pitchfork REMOVED from the model and all clips (the hero
carries nothing until a weapon is designed). Queued behind the
in-flight s7 sash lane (same files).

## 2026-10-04 — Wren in-game verdicts (artist, verbatim)

"we want his hands closed and not holding anything" + "his leg goes
up too much right now in the run animation and in the game itself
its the walk animation doesnt seem to properly cycle same for the
run animation" + "the walking and camera doesnt seem to match
pokemon sun and moon style".
(1) HANDS: closed (relaxed fists), both hands, all clips — never a
grip/holding read. CORRECTION (artist, verbatim): "he shouldn't hold
anything including weapons unless he's in combat scenarios" —
BINDING RULE: Wren's out-of-combat clips (idle/walk/run, overworld)
are permanently empty-handed; any future weapon appears ONLY in
combat-scenario clips (the HAS_FORK-style prop machinery serves
those, never locomotion).
(2) RUN: knee drive too high — lower it from thigh-horizontal; keep
the young-hero sprint identity otherwise.
(3) IN-GAME DEFECT: walk and run do not cycle properly in the game
(forge loop seams are 0.0 — the break is game-side playback).
(4) DIRECTION: overworld walking + camera should match Pokemon
Sun/Moon style (smooth continuous follow, not stepped).

## 2026-10-04 — Wren posture verdicts (artist, verbatim + 2 markups)

"wren needs both arms to swing, and the cape not to be stuck to his
arm" + "his legs are also angled inward it should be more like the
second picture with a more regular standing up posture and carry
over to his running, more like the motion we did with the werewolf
character, currently it looks like his legs are broken."
(1) ARMS: BOTH arms swing freely in walk and run (this ANSWERS the
open right-arm question: free swing, yes). The left hand releases
the cloak strap in locomotion; the cloak must read free of the
arms, never pinned to a fist.
(2) LEGS: current stance is knock-kneed (markup 1: both shins angle
inward). Required: regular upright stance, legs tracking straight
(markup 2's vertical line), in idle AND carried through walk/run.
Werewolf project's locomotion work is the motion reference.
(3) Overworld pace: artist bumped (free preset 2.2/5.0 m/s).

## 2026-10-04 — Compendium import of the 6; Elias staff/robe; Varden hair

Artist (verbatim): "work with a conquest agent to import these with
default values firefly, firesprite, magmoo, supaoctto, vampito,
vampwarrior ... rebalancing and scaling can be done once theyre in
the game, dont attach them to anything yet just surface them in the
compendium" + "before the elias and varden go in, he is not holding
his staff its just attached to his robe and his robe stretches and
we never got the hair update on varden I believe".
(1) THE 6 go into Conquest as roster entries with DEFAULT/placeholder
values, clearly marked for rebalancing; referenced by NOTHING (no
spawns/encounters/story) — compendium visibility only.
(2) ELIAS defects before he ships (screenshot on file): the staff is
not IN his hand — it floats attached beside the robe (grip not
applied at rest); and the robe skirt STRETCHES (weight defect).
(3) VARDEN never received the sheet-matched hair treatment — scalp
rebuild per his saved sheet (swept-back tousled + head detail
panel), mass-first, same as Elias's approved v5 scalp.

## 2026-10-05 — NEW UNIT: Shadow Assassin (Varrick's ally) — sheet delivered, v1 draft ordered

Artist (verbatim): "begin work on creating the model for this
reference, follow guides for our previous work" — with the Shadow
Assassin character sheet attached (front/side/back + head, necklace,
belt, blade, cloak detail panels + shadow-ability concepts + 5-chip
palette). Sheet saved: design/reference/shadow_assassin/
shadow_assassin_sheet.webp (1536x1024, transcription-time save per
the style guide's sheet law).
Sheet facts binding for the build: hooded figure, NO FACE VISIBLE
(dark void + face wrap under the hood — no eyes/mouth/face paint);
layered tattered black/charcoal cloak + hood with gold trim, purple
accents (scarf, gold diamond pendant, back-cloak diamond sigil,
under-layer); leather shoulder plates, crossed chest straps with
round gold brooch; belt with gold rings + pouches; tattered layered
skirt over dark trousers; buckled boots; large curved dark blade(s)
with purple sigils. Role: Varrick's ally, infiltration/assassination,
shadow magic.
(1) v1 DRAFT lane (two-speeds law): full house-style build minus the
face stack (hood void replaces it), palette PIXEL-SAMPLED from the
saved sheet, blade hold baked into the bind pose (clipless law), one
comparison sheet vs the SHEET views.
(2) Open artist questions filed under S-numbers in OPEN-QUESTIONS.md
(blade count, sigil glow, movement intent — batched with the v1
renders).

## 2026-10-06 — NEW UNIT: Lyra (student researcher, Elias' student) — sheet delivered, v1 draft ordered

Artist (verbatim): "start next one" — with the Lyra character sheet
attached (front/side/back + head, torso, shoulder, belt, bag, cloth
pattern detail panels + academy inspiration art + compass-star
symbol/motif + 8-chip palette). Sheet saved:
design/reference/lyra/lyra_sheet.webp (1536x1024).
Sheet facts binding for the build: AGE 17, female, FULL FACE VISIBLE
(complete house-style face/eyes/mouth stack applies); dark-brown
hair in a HIGH PONYTAIL with loose fringe strands, blue ribbon +
gold hairpiece with tassel; white collared shirt, blue necktie
ribbon with gold diamond pin; navy shoulder capelet/mantle with gold
trim + the compass-star emblem (uniform style matching Elias, per
the sheet's own notes); brown cross-chest strap; dark corset belt
with buckles + hanging vials/instruments; cream/parchment skirt
panels (gold-trimmed, diamond motif) over black trousers; leather
satchel + scroll case with vials; brown lace-up boots; carries a
small STACK OF BOOKS in one arm (front + side views). Role: student
researcher, peer to Wren, kind/determined/reliable. No magic shown.
(1) v1 DRAFT lane (two-speeds law): full house-style build INCLUDING
the face/eyes/mouth/hair stack (Wren reference implementation, Ashe
ratios, mass-first ponytail per the sheet views), palette
PIXEL-SAMPLED from the saved sheet, book-stack hold baked into the
bind pose (clipless law), one comparison sheet vs the SHEET views.
(2) Open artist questions filed under L-numbers in OPEN-QUESTIONS.md
(height, book hold, glow, ribbon dynamics, movement intent — batched
with the v1 renders).

## 2026-10-06 — Lyra v1 verdicts (artist, verbatim + 3 markups)

"hair should extend into the red section" + "also what is the weird
discoloration on her neck" + "also her chin is too large and should
only go to where the red line is".
Markups saved: design/reference/lyra-v1-hairline-annotation.webp
(red line above/behind her left ear), lyra-v1-neck-annotation.webp
(orange circle, front of neck above the collar),
lyra-v1-chin-annotation.png (red line under the chin, front view).
(1) HAIRLINE: bare skin above/behind the ear — the scalp hair must
extend DOWN into the marked region (the resume's HAIRLINE_SIDE arch
over the ear left the zone above it uncovered).
(2) NECK DISCOLORATION: investigate the marked patch as a defect —
find the actual cause (region paint leak / AO / normal artifact),
never just repaint over it.
(3) CHIN: too large — shorten to the marked line (front view); part
of the earlier "boyish" read, face probe ratios re-checked after.

## 2026-10-06 — Lyra v1.1 verdicts (artist, verbatim + 2 markups)

"we dont need the shadow  it looks like discoloration" + "if we
want the sharper jaw line then it should be made on the 2nd image
where i drew red line by having it creased or stick out more there".
Markups saved: design/reference/lyra-v11-neckshadow-annotation.png
(red tick at the shadow edge under the ear),
lyra-v11-jawcrease-annotation.png (red line tracing the jaw edge
from below the ear forward under the chin).
(1) JAW/NECK SHADOW: REMOVE it on Lyra — even smoothed it reads as
discoloration. The skin_shadow cast-shadow region under the jaw
goes; skin reads one tone there.
(2) JAW DEFINITION IS GEOMETRY, NOT PAINT: a sharper jaw line, if
kept, is a CREASE/protrusion along the marked line (below the ear
sweeping forward under the chin) — the jaw edge sticks out / folds,
never a painted band. (Scope: Lyra now; candidate house rule for
the next face builds.)

## 2026-10-09 — NEW UNIT: Seedant (artist reference sheet) — first run of the kickoff process

Reference saved: design/reference/seedant-character-sheet.webp — "SEEDANT /
CHARACTER REFERENCE", three views (front / back / side) + four detail panels
(face, leaf, leaf arm, pot) + a five-chip palette. Intake (measured, not
eyeballed): design/reference/seedant/intake.json + intake_overlay.png.

Artist (verbatim): "create the model for this reference it should follow
the pattern and design done for the firefly jet creature we previously did"
+ "this should be a process we can kick off rather than you doing
everything directly".

Binding readings:
(1) PROCESS: new from-scratch units are now KICKED OFF, not hand-built by
the orchestrator: skill .claude/skills/conquest-new-unit (intake ->
scaffold -> spec -> one background build lane -> gate + show). Tools:
improve/sheet_intake.py, improve/new_unit_scaffold.py,
improve/templates/new_unit/ (runner/render/clips/compose/bake-diff
templates, BUILD_CONTRACT.md, lane_brief.md.tmpl). Seedant is its first run.
(2) SEEDANT = a new unit "seedant" built to the FIREFLY PATTERN (SDF organic
body + closed parametric parts, sheet units 1 = 100 px, palette regions,
bake, rig, glb, digest twins). Sheet transcription:
- A small green plant creature sitting IN a terracotta pot; whole unit
  ~5.0 units tall (pot base y 746 -> red leaf tips y ~246), pot ~3.6 wide.
- BODY: one soft green gumdrop/bean (head + body one piece, no neck), top
  of the head ~y 340 front, filling the pot mouth and sinking into it.
  Smooth, puffy, organic; no hard edges.
- FACE (face panel: "simple, cute expression / round eyes / small mouth"):
  two tall glossy black oval eyes with a white highlight dot (top), set
  close; a small open smile with a pink tongue below and between them.
- SPROUT CURL: a short stem rising from the top of the head curling into a
  fiddlehead spiral (darker green outline).
- LEAF ARMS (panel: "rounded shape / curl at tip / smooth gradient shading /
  organic plant look"): two plump leaf-lobe arms from the shoulders that
  DRAPE OVER the pot rim and hang down the outside wall; each tip carries a
  darker-green spiral curl. They rest ON the rim — never through it.
- RED LEAVES (panel: "plant-like leaves / subtle veins / tapered shape / red
  accent"): two tall tapered leaves rising from the back-sides of the body
  behind the head, splayed outward in a V (front), leaning slightly back
  (side); a midrib + side veins, slightly folded along the midrib.
- POT (panel: "terracotta clay pot / simple shape / subtle texture,
  scratches / worn edges"): tapered flower pot with the classic thick rolled
  rim band (rim y ~480-585 front), visible rim thickness, slightly worn
  lighter edges.
- PALETTE (pixel-sampled chips): body green 174,205,70; leaf red 233,69,65;
  terracotta 154,77,46; dark terracotta 108,66,50; pale green highlight
  201,222,148. Curl / outline green and eye black sampled off the drawing.
- No glow on the sheet: _GLOW all zero (Varden pattern).
- Tier REGULAR window [8000, 22000] (a line unit like the firefly; expected
  to land low in the window — simple forms).
- MOVEMENT not given: idle + walk ship as PLACEHOLDERS (gentle sway / leaf
  rustle / blink; an in-place pot hop) until the artist answers S1.

## 2026-10-09 — NEW UNIT: Shadowbun (artist reference sheet) + Seedant answers + size lineup

Reference saved: design/reference/shadowbun-character-sheet.webp — "SHADOWBUN /
CHARACTER REFERENCE", three views + four detail panels (face, ear, tail, belly)
+ a five-chip palette. Intake: design/reference/shadowbun/intake.json +
intake_overlay.png (run with --ortho-views back,side — see (1)).

Artist (verbatim): "intake this as well, ceate 3d model as well for it.
alternate colors can be done at a later time, that animation is fine as
well, sizing would help to see our all conquest creatures in a line so can
compare and give reference to size"

Binding readings:
(1) SHADOWBUN = new unit "shadowbun" through the kickoff process (firefly
pattern). The sheet's "FRONT" is drawn THREE-QUARTER (face in profile to
image-left, belly patch to one side, tail showing): build from the true
orthos BACK + SIDE, use the front only as a 3/4 check. Sheet transcription:
- A plump black shadow-bunny ~6.0 units tall: one tall EGG/PEAR body with no
  neck (the head is the narrow top of the egg), a slightly protruding rounded
  muzzle at the front-top. Fluffy FUR read: fringed / tufted silhouette edges
  everywhere on the black fur (body, arms, legs).
- FACE (panel: "simple, cute expression / round eye shape / soft,
  cloud-like mouth / minimal features"): a round WHITE eye dot on the side of
  the head (every view shows one: rabbit-style lateral eyes, one per side —
  question B2); a white "cloud-like" toothy GRIN: a row of soft rounded white
  teeth-bumps along the muzzle's front-lower edge.
- EARS (panel: "leaf-like shape / soft, fluffy fur / slightly tapered tips /
  light gray-lavender"): two upright leaf-shaped ears, jagged fur edges,
  slight V, accent lavender.
- BELLY (panel: "round belly patch / scalloped top edge / smooth, painterly
  shading / light gray-lavender"): a large round lighter-lavender patch on
  the front of the egg, its top edge SCALLOPED (cloud bumps) with a thin
  pale highlight line along the scallops.
- ARMS: short thick fluffy black arms hanging close at the sides, tufted ends.
- LEGS: short stubby legs, big rounded feet with toe clumps.
- TAIL (panel: "large, fluffy tail / cloud-like shape / soft, rounded fur /
  light gray-lavender"): a very large cloud-puff tail low on the back (about
  a third of the body height), lighter lavender, made of rounded fur clumps.
- PALETTE (pixel-sampled chips; the sheet's labels sit one row off their
  chips — mapped by colour): main fur 32,30,28 (drawn fur samples 31-35);
  accent / ears 100,87,97; belly + tail 148,133,143 (shaded 127,112,124);
  eye + mouth 242,243,240; shadows / outline 108,108,108.
- Fur detail approach (lane decides, reports): silhouette tufts in the SDF
  (directional tuft field / clump cones) + fur strokes carried by the baked
  normal map from the SDF high; ears and tail as rounded clump unions.
- Glow: none by default (question B3). Tier REGULAR [8000, 22000].
- Clips PLACEHOLDER: idle (breathing, ear twitch, tail fluff, arm sway) +
  walk (in-place bunny hop on the stubby legs) — question B1.
(2) SEEDANT answers: S4 skins -> LATER (default only for now); S1 the
placeholder clips described (sway / leaf rustle / arm taps / blink + pot
hop) are FINE (approved as the plan; renders still to be seen).
(3) SIZE LINEUP (new deliverable, queued as a lane when a lane slot frees —
WIP is 2 with seedant + shadowbun in flight): one render of EVERY Conquest
creature standing in a row on a shared floor with a height ruler, at a
common scale, so the artist can compare and set relative sizes. Reusable:
rerunnable whenever a unit lands. Answers S2-type size questions for all
units at once.

## 2026-10-09 — Shadowbun answers (artist verbatim, lane in flight)

"floaty waddle,2 eyes one on each side, can add a small glow, this reference
didnt give a full front reference but should be like a thick bunny"

Binding readings (delivered to the v1 lane by resume):
(1) B1 WALK = a FLOATY WADDLE: side-to-side rocking steps on the stubby legs
with soft hang time (the body lifts a little and drifts down between steps,
eased landings, no hard impacts); ears + tail trail softly. Replaces the
placeholder bunny hop. Idle stays as planned.
(2) B2 EYES = TWO, one on each side of the head (lateral, rabbit-style).
(3) B3 GLOW = SMALL: the white eye + grin get a faint emission (low tier,
well under the 2.0 phone cap); everything else stays at zero glow.
(4) FRONT = a THICK BUNNY: the sheet has no straight-on front. The BACK view
is a true ortho, so it gives the front's width (mirror it). The body must
read full, round and wide from the front — never slimmed toward the 3/4
drawing.

## 2026-10-09 — Shadowbun v1.1 verdict (artist verbatim + markup)

"for shadowbun only fix is to  move the eyes slightly more inward closer to
the red outline shown"
Markup saved: design/reference/shadowbun-v11-eye-annotation.png (front
render; red circles inside each eye, ~0.67 of the current eye spacing, same
height).
Binding reading: v1.1 is otherwise ACCEPTED as-is (incl. the residual arm
nubs) — the ONLY change is eye placement: EYE_AZ 58 -> 34 deg (measured on
preview renders to land on the marked spacing), EYE_Z unchanged. Parameter
tweak: orchestrator edit + runner rerun, no lane.

## 2026-10-09 — Gates: Seedant v1.2 + size lineup delivered

- SEEDANT v1.2: lane rebuilt the arms as flattened leaf-lobes (v1.1); the
  orchestrator gate found the lobe's ellipsoid tiling visible as a quilted
  pattern -> ARM_K 0.03 -> 0.07 (previews 0.03 / 0.07 / 0.11), which swelled
  the lobe 0.03 into the pot (clearance -0.025) -> ARM_STANDOFF 0.03 restores
  +0.0038. Twins byte-identical incl bakes.
- SIZE LINEUP: improve/lineup_render.py + lineup_run.ps1 (22 units, 18 s),
  renders/lineup/, design/size-table.json (artist edits target_height_m).
  Measured: in-game scale = the cell-fit formula for all 19 delivered units
  (Conquest .import root_scale; no roster model_scale set). Finding: sizes
  are an artefact of the cell fit, not design (see OPEN-QUESTIONS Z1).

## 2026-10-09 — Size targets (artist verbatim) + Seedant round pot / plumper arms

"round pot and plumper" (Seedant) and the size list:
"magmoo 8 feet / petalfang 4 feet / barkling 8 feet / blightcap 2 feet /
vamp warrior 6 feet 2 / firesprite 4 and half feet / varden 6 feet 4 / lyra
5 foot 8 / shadow assassin 6 feet / mortis 6 feet 2 / seedant 3 and half
feet / vineweave 5 ft 6 inches"

Binding readings:
(1) SIZE TARGETS written to design/size-table.json target_height_m (feet ->
metres: 2.438 / 1.219 / 2.438 / 0.610 / 1.880 / 1.372 / 1.930 / 1.727 /
1.829 / 1.880 / 1.067 / 1.676). Units not listed keep today's size. The
lineup now labels in FEET + INCHES (lineup_render.py LABEL_UNITS="ft").
(2) FOOTPRINT CONSEQUENCE (measured: footprint scales with height): at its
target, MAGMOO is ~26 ft 10 in long (8.2 m: its rest pose lies along the
floor), BARKLING ~10 ft 11 in wide (3.3 m) and PETALFANG ~8 ft 11 in wide
(2.7 m). All three exceed a 1-cell unit (1.9 m); barkling + petalfang fit
the 2x2 boss cell (3.8 m), magmoo fits no current cell. Artist decision:
multi-cell units, or measure "8 feet" another way (e.g. magmoo's arched-up
head height), or a tighter pose. Everything else stays inside one cell.
(3) Applying targets IN GAME is a per-unit delivery change (Conquest
.import root_scale / roster model_scale) — not started; awaits (2).
(4) SEEDANT v1.3: POT_DEPTH 0.74 -> 1.0 (round); ARM_AZ 50 -> 44 with the
lobes x1.45 wide / x1.18 thick (preview A/B vs the sheet front);
ARM_STANDOFF 0.03 -> 0.062 (the thicker lobes measured -0.032 into the
rim at 0.03; previews 0.062 -> +0.0055).

## 2026-10-09 — SDF kit zero-value fix (artist: "fix that sdf issue ... from worktree path")

Run as an isolated worktree lane, then brought back as code only. The patch
(4 files, +22/-5) went into main: magmoo_sdf.polygonise pushes |F| <
NODE_EPS * step (0.005, derived: >= 10 float32 ulps of crossing clearance at
the worst measured slope L/step = 412 /m) off zero on a copy. All runners
now resolve $P from their own folder (worktree-safe). Worktree evidence:
firefly / firesprite / magmoo checks unchanged, twins identical (firesprite
except bake_normal within the gate), retopo p99 within noise, bbox
unchanged. firesprite NODE_EPS 0.02 KEPT (removing it was worse on 5/8
parts). Outputs regenerated in MAIN by rerunning the runners (the worktree
outputs embed worktree paths); seedant + shadowbun re-pinned too.

## 2026-10-09 — Conquest: true sizes + import the missing units (artist verbatim)

"yes can remove that worktree, conquest now shouldnt resize check the model
sizes compared to what we have -- have an agent do it / and yes commit our
changes / and impot the new models into conquest" + "yes correct sizes to
what I specified and import the missing ones in as well"

Binding readings: Conquest already shows units at design height (its commit
e8974f2: roster model_scale, fixed 2 m grid, wide units overhang one tile,
multi-cell only for giants). A Conquest lane (a) measures every unit
in-engine against design/size-table.json and CORRECTS every artist-
specified unit to its target via model_scale. MAGMOO goes to 8 ft (no
longer held, ~8.5 m nose-to-tail, one tile, overhangs). (b) Imports EVERY
forge unit not yet in the game: seedant (3'6"), shadowbun (1.80 m, artist
size open), shadow_assassin (6') as compendium placeholders per the
import-wave precedent. Answers Z2: no multi-cell for magmoo / barkling /
petalfang. They overhang.

## 2026-10-09 — NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)

Artist (verbatim): "these 3 are next" (Bombee, Aquaoct, Canine Bat Hybrid) +
"next intake" (Raven Rogue) + "load them in and start the process".
Sheets were pasted inline, then loaded off the clipboard by a watcher:
design/reference/{bombee,aquaoct,canine_bat,raven_rogue}-character-sheet.png
(originals in design/reference/incoming/). Intake: design/reference/<unit>/
intake.json + intake_overlay.png. All four are in tier REGULAR [8000,
22000] and ship with idle + walk PLACEHOLDER clips (movement asked, never
guessed). No artist size yet: each goes in at its natural sheet scale, and
the size table takes it.

BOMBEE (true orthos BACK + SIDE; the front is drawn 3/4). A round, almost
spherical fuzzy bee body (~4.3 units incl. wings).
- Front: a pale cream face disc covers the front. Two round, shiny
  dark-brown eyes with a highlight, a small smile, pink blush cheeks.
- Body: bold black + yellow bands wrap the body behind the face. From the
  back, the bands circle a large conical cream-tan STINGER pointing back.
- Wings: two upper wings, large leaf / petal shapes, slightly translucent
  cream with a tan diamond vein lattice. Two small lower wings.
- Underside: four small conical cream-tan spikes (feet).
- Palette: cream 253,235,207 / yellow 254,198,92 / black 45,41,40 /
  tan 229,190,148 / blush 249,169,141.
- A flyer: placeholder idle = hover bob + wing buzz, walk = hover-drift.
  The translucent wings use the firefly alpha path.

AQUAOCT (true orthos FRONT + SIDE). An octopus.
- Head: a tall, glossy, egg-shaped pale-aqua head, more than half the
  height, with rounded darker teal spots in varied sizes.
- Face: a goggle-like cream / white MASK around two big round eyes (teal
  iris, highlight). The side view shows its round strap button. Below it,
  a small beak-like mouth with a scalloped white "beard" frill.
- Tentacles: EIGHT leaf-like flowing tentacles with soft wavy edges, aqua
  above and cream-white below. They splay out and curl down, and it stands
  on the tips.
- Palette: 215,236,231 / 164,215,206 / 109,178,175 / 75,130,144 /
  39,59,64.
- Placeholder: idle = tentacle sway; walk = a tentacle-shuffle glide.

CANINE BAT HYBRID — unit "canine_bat" (true orthos BACK + SIDE; the front
is drawn 3/4). A QUADRUPED dog-bat (~6.0 units incl. ears).
- Ears: OVERSIZED upright pointed ears, a thin membrane with a mauve
  inner surface and soft fur at the base.
- Face: big round dark eyes with a pale rim, a distinctive pig / bat snout
  (mauve-grey), small fangs.
- Body: a fluffy pale neck RUFF, short soft dark plum-charcoal fur, short
  sturdy legs with rounded paws, lighter fur on the lower legs / paws, a
  small tail.
- Palette: 37,34,37 / 87,76,84 / 189,169,165 / 137,106,116 /
  104,74,89.
- NO wings drawn (question C1).
- The first four-legged forge creature: placeholder walk = a 4-beat
  quadruped trot.

RAVEN ROGUE — unit "raven_rogue" (true orthos FRONT + SIDE). A chunky
upright raven (~5.3 units).
- Plumage: dark purple-navy, a cream chest bib, a LARGE curved dark-grey
  beak.
- Face: heavy-lidded intense pale-gold eyes, a red slash scar across one
  brow, a small fang, a swept head crest.
- Wings: layered CAPE-like wings draped around the body, with torn /
  ragged tips that grade to violet.
- Legs + tail: short legs with simple leg bands, clawed grey feet, a short
  ragged tail.
- Palette: 53,49,59 / 70,65,84 / 101,88,112 / 229,208,184 /
  142,68,65 / 103,94,93.
- No weapon on the sheet. Placeholder: idle = a cape-ruffle stance; walk =
  a hop-strut.

## 2026-10-09 — Bombee v1 verdict (artist verbatim + 2 markups)

"get rid of these extra wings on bombee and the main wings staying should
clip in as much and should be more out like the red"
Markups: design/reference/bombee-v1-lowerwing-annotation.png (red X on both
small lower wings), bombee-v1-wingroot-annotation.png (a red outline drawn
round the upper wing, ABOVE / outside the body line).
Binding reading: (1) REMOVE the two small lower wings: geometry, bones,
clip keys, everything. (2) The two main wings must NOT clip into the ball as
much: roots seated shallowly on the top surface, with the petal raised and
splayed outward so the whole leaf stands above / outside the body silhouette,
per the red outline.

## 2026-10-09 — NEW UNITS x2: Pangolin Knight, Shadow Mole (artist sheets)

Artist (verbatim): "this is a new intake" (Pangolin Knight) + "also begin
shadow mole". Sheets: design/reference/pangolin_knight-character-sheet.webp,
shadow_mole-character-sheet.png (the latter came off the clipboard). Both
have intake.json + overlay. Both sheets draw the FRONT three-quarter, so the
true orthos are BACK + SIDE. Tier REGULAR [8000, 22000]. Idle + walk are
PLACEHOLDER. Natural sheet scale; the size table takes them.

PANGOLIN KNIGHT ("pangolin_knight", ~5.9 units). An upright bipedal
pangolin.
- Face: cream face / chest / belly with soft fluffy fur; a short snout
  with a pink nose tip; a simple expressive black eye.
- Head: HELMET-like overlapping head scales with a crest ("simple layered
  scales, smooth rounded shapes, not too many scales").
- Body: covered in layered, overlapping, rounded-pointed brown SCALES in
  rows, the back a stack of large leaf scales. A LONG SEGMENTED tapering
  TAIL of scaled bands curves down to the floor behind.
- Forelimbs held forward with armour-band GAUNTLETS and dark brown claws.
  Short legs, clawed dark feet.
- Palette: 70,52,47 / 131,87,64 / 177,114,81 / 203,147,102 / 243,208,170
  / 71,59,57.
- No weapon drawn (question P1).

SHADOW MOLE ("shadow_mole", ~3.8 units). A near-black mole emerging from
a SHADOW BURROW.
- Burrow: a soft, cloud-edged, near-black disc on the floor, with the
  creature rising from it.
- Body: a smooth rounded dome body + head; a small simple white eye with a
  dark pupil.
- Stalks: pink BURROW-STALKS. One tall and two short, long and flexible,
  each ending in a flower-like petal tip with a gradient from bright pink
  to pale pink. A pink petal crown sits on the head.
- Claws: two bright pink DIGGING CLAWS resting on the burrow rim, rounded
  with three pointed toes.
- Palette: body 22,14,25 / shadow 23,15,26 / pink 230,47,132 / highlight
  227,148,189 / accent 186,34,105.
- Glow: none by default (question M1: should the pink glow?).

## 2026-10-09 — Sizes for the six + Shadow Mole glow (artist verbatim)

"bombee is about 3 feet big and floats / canine bat is also 3 foot 5 /
pagolin knight is 3 feet / shadow mole sticks out of the ground to a height
of maybe 2 and a half feet, yes glow / aquaoct is around 3 feet as well /
raven rogue is 3 feet as well"
Applied (Conquest 2nd commit after cb0a8ca): bombee 3 ft as its body EXTENT,
with the idle hover lifting it so its top is ~3 ft 3 in. aquaoct,
raven_rogue and pangolin_knight 3 ft; canine_bat 3 ft 5 in; shadow_mole
2 ft 6 in out of the ground. M1 ANSWERED: Shadow Mole's pink stalks /
petals / claws glow faintly (palette tiers 0.15-0.50; the build's gate now
requires exactly those regions to glow, under the 2.0 cap).

## 2026-10-10 — NEW UNITS x4: Soot Puff, Winged Cat, Phoenix Hatchling, Chest Monster (artist sheets)

Artist (verbatim): "go ahead and create the model for this one following our
exisiting pipeline" (Soot Puff) + "here is another one to take in" (Winged
Cat) + "next one" (Phoenix Hatchling) + (Chest Monster, no text).
Sheets: design/reference/soot_puff-character-sheet.webp (attachment);
{winged_cat,phoenix_hatchling,chest_monster}-character-sheet.png (pasted
inline and loaded off the clipboard; originals are in
design/reference/incoming/). Intake: design/reference/<unit>/intake.json +
intake_overlay.png. All four are in tier REGULAR [8000, 22000]. Idle + walk
are PLACEHOLDER (movement is asked, never guessed). They go in at natural
sheet scale, and the size table takes them.

SOOT PUFF ("soot_puff", ~4.1 units; all three views are true orthos; axes
pinned to the body centre: front 229 / back 639 / side 1009.5).
- Body: one round, near-black puffball. The edge is soft, fuzzy, irregular
  and painterly ("slightly airy").
- Material: matte, dusty, no sheen.
- Face: two white oval eyes plus one small white oval mouth.
- Arms: two soft, fuzzy, dangling arms hang below the ball. They have no
  hands or fingers.
- No legs. It FLOATS: the sheet's shadow sits ~0.6 u under the arm tips.
  Rest pose min z = 0, and the placeholder idle = a hover bob + arm sway.
- Palette: body 0,0,0 / fuzz 15,15,15 / dusty rim 113,113,115 /
  eyes + mouth 255,255,255.
- Deviations to report:
  (a) The front view's mouth sits ~0.1 u right of the eye midline. It is
      built on the centreline.
  (b) The side view draws the near arm ~0.85 u forward of the body centre.

WINGED CAT ("winged_cat", ~4.3 units; true orthos BACK + SIDE; the front is
drawn 3/4; front/back fused by the tail, so --cuts 465,790).
- Pose: a SITTING green cat. Big round head, pointed ears with cream inner
  ears, haunches, two front legs with paws.
- Face: almond white eyes with black slit pupils, an open mouth with a white
  interior, two cute fangs.
- Collar: a fluffy red-orange collar wraps the neck.
- Wings: two leaf / bat-shaped bright-green wings attached to the back.
  They have a scalloped trailing edge and darker-green veins.
- Tail: a broom / fan-shaped tail of layered dark-brown blades, fanning
  behind as one broom (a fan from both the back and the side views; the side fan
  bottom edge sits at ~z 0.47, NOT on the floor; corrected at the gate).
- Palette: green 24,180,60 / dark green 18,106,41 / collar 245,44,29 /
  tail brown 69,27,18 / cream 245,237,229.
- Deviations to report: the black ink outline is drawing style and is not
  modelled.

PHOENIX HATCHLING ("phoenix_hatchling", ~6.5 units incl. crest; all three
views are true orthos).
- Head: a big round maroon head, more than half the height, topped by a
  curled feather crest (swirl curls) with small ember particles.
- Face: large round glossy black eyes with a white highlight, a small
  orange beak.
- Collar: a GLOWING ORANGE RING at the head base, then a soft smoky
  near-black fluff collar with floating ash flecks.
- Body: a small round taupe body with feathered wing shapes at the sides
  and a short tail tuft behind (side view). An orange EMBER JEWEL (diamond)
  sits on the chest.
- Legs: thin pink bird legs with ring segments, three-toed feet plus a back
  toe.
- Palette: maroon 79,20,17 / orange 245,143,28 / taupe 117,87,80 /
  pink 232,129,122 / smoke 27,23,22.
- Glow (drawn on the sheet): the collar ring, the chest jewel and the
  embers.
- Deviations to report: embers and ash are static low-poly flecks (no
  particle systems).

CHEST MONSTER ("chest_monster", ~5.5 units incl. crystals).
- ALL THREE VIEWS ARE DRAWN IN 3/4 PERSPECTIVE; there is no true ortho.
  Intake uses front + side with face-centred axes (192 / 578 / 1010), and
  the overlay is indicative only. The lane reconstructs the box from the
  perspective views and reports its proportions.
- Body: a slightly tapered tan box (wider at the base). A dark-brown lid
  band overhangs it all round.
- Eye: a single orange almond EYE EMBLEM on the front, with a dark-brown
  iris and a highlight.
- Crystals: blue crystal clusters grow from the lid (a big cluster plus a
  small one). They are angular prisms with white facet streaks, slightly
  translucent and glowing.
- Magic: soft pinkish glowing magic smoke bursts from the top opening.
- Legs: four tiny dangling legs (brown stalks with orange dots), each
  ending in three clawed red-orange toes with an orange-red glow.
- Palette: box tan 210,154,99 / lid brown 71,40,17 / eye orange
  253,144,56 / feet red-orange 253,97,44 / crystal blue 73,79,253 /
  magic pink 252,184,170.
- Glow (drawn on the sheet): the crystals, the feet and the smoke.
- Deviations to report: the smoke is a translucent glow-burst mesh
  (firefly alpha path), and the sparks are static flecks.


## 2026-10-10 — Chest Monster v1 verdict (artist verbatim + markup)

"lets get rid of the shiny sparkle  on the center of the creature head, marked in red"
Markup: design/reference/chest_monster-v1-smoke-annotation.png (a red mark
on the pink star burst at the centre of the lid).
Binding reading: REMOVE the magic-smoke burst (the core + 13 rays) from the
lid. The small spark flecks and the crystals stay. Applied by the TUNABLE
SMOKE_BURST = False (the build no longer emits the parts; the palette keeps
the regions).
Follow-up (artist verbatim): "yes turn off the floating spark flecks".
Applied: SPARKS count 0. The lid now carries the crystals only.

## 2026-10-10 — Phoenix Hatchling v1 verdict (artist verbatim)

"the smokey collar should be half that height and thinner in the center"
Binding reading:
- The smoke collar's vertical thickness is halved (the v1 tube is 0.84 u
  tall; target ~0.42 u at the sides).
- It tapers thinner toward the front and back centre line (X = 0), so it is
  thickest at the sides, as the front view draws it.
- The head stays seated in the collar with the glow band at its base, and
  the body's top meets the collar's underside with no gap.

## 2026-10-10 — NEW UNITS x3: Petal Cobra, Crystal Canine, Leaf Bird (artist sheets)

Artist (verbatim): "next one" (Petal Cobra) + "another one" (Crystal Canine)
+ "another one" (Leaf Bird) + "its not a leaf fish its leaf bird descriptor
is wrong in case that weighs in to design choices".
Sheets (pasted inline, loaded off the clipboard; originals are in
incoming/): design/reference/{petal_cobra,crystal_canine,leaf_bird}-
character-sheet.png. The Leaf Bird sheet is titled "LEAF FISH"; the artist
says it is a BIRD, so the unit is "leaf_bird", and its lateral leaves are
WINGS and its back leaf a crest/wing-leaf, not fins. Tier REGULAR
[8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet scale.

PETAL COBRA ("petal_cobra", ~5.9 u; three true orthos; --cuts 484,862;
axes pinned front 240 / back 673 / side 1000).
- Body: a pale-sage serpent (215,219,202) rising upright from a long,
  smooth, tapering tail.
- Tail: it curves to the unit's RIGHT and forward, its tip resting on the
  floor. The front view sweeps image-left, the back view image-right, and
  the side view forward under the head.
- Head: a smooth rounded head, with narrow almond amber eyes (245,212,154)
  and a gentle smile line.
- Hood: two LARGE CURVED HOOD PETALS rise behind the shoulders and flank the
  head.
  - Inner face: green 127,146,121 shading to 76,95,86, with light veins.
  - Back / rim: cream 225,205,170, its tip curling over at the top.
  - Side view: the petal stands behind the body and arcs up and forward.
- Torso: a front panel with 3 pairs of oval amber MARKINGS (symmetrical),
  and the torso's lower corners flaring into two small nubs.
- Palette: 215,219,202 / 127,146,121 / 76,95,86 / 225,205,170 /
  245,212,154 / 41,51,47 (ink).
- Deviations to report:
  - The front and back draw the two petals at different heights per side;
    the build is symmetric.
  - The tail curl is asymmetric, as drawn.

CRYSTAL CANINE ("crystal_canine", ~6.2 u; --cuts 418,760; axes front 205 /
back 565 / side 1005.5; the side view is a true ortho, and the front and
back are near-frontal with the tail swinging round).
- Body: a slim standing four-legged canine in cream-white fur
  (247,234,224), with large pointed ears (blush inner) and a fluffy chest
  ruff.
- Face: expressive grey eyes, a simple cute muzzle, a small dark nose.
- Gem: a faceted FOREHEAD GEM (stone / crystal, mauve-taupe), integrated
  into the head.
- Mane: a CRYSTAL MANE / stone frill of layered angular faceted prisms
  (217,188,180 lit, 153,137,146 shade) growing from the neck / shoulders,
  radiating out and back and blending into the back, integrated with the
  fur.
- Tail: a long, flowing, plume-like tail sweeping down toward the floor.
- Palette: 247,234,224 / 217,198,198 / 217,188,180 / 153,137,146 /
  81,74,81.

LEAF BIRD ("leaf_bird"; --cuts 440,712; side axis 960; front + side used as
orthos).
- Pose: a streamlined LEAF BIRD drawn in a DIVE. The side view pitches the
  body ~35 deg nose-down, beak lowest, tail up and back. The front (nose-on)
  and back (tail-on) views show the same dive, so their heights differ (4.5
  / 5.1 / 4.3 u); reconcile them against the side view.
- Body: bright green (56,191,33), with dark-green (4,115,25) leaf-vein
  segment bands along the belly and light-green highlights (121,190,91).
- Beak: a pointed amber beak (200,133,30).
- Eyes: round off-white (254,248,220) with an amber iris.
- Wings: two large swept leaf WINGS at the sides (front + back views).
- Back crest: a leaf crest / wing-leaf on the back with a vein net (the
  sheet's "dorsal fin" panel), plus a central crest leaf and two small
  leaves (front view).
- Tail: a tapered leaf tail with a sharp tip and a small second lobe.
- Rest pose = the drawn dive, with min z 0 at the lowest point.

## 2026-10-10 — NEW UNITS x2: Eye Totem, Ember Eye (artist sheets)

Artist (verbatim): "next one" (Eye Totem) + "next one" (Ember Eye).
Sheets (clipboard): design/reference/{eye_totem,ember_eye}-character-
sheet.png. Both FLOAT: a floor shadow is drawn well below each. Rest pose
min z = 0 at the lowest tip, and the placeholder idle = a hover bob. Tier
REGULAR [8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet scale.

EYE TOTEM ("eye_totem", ~6.7 u; orthos FRONT + BACK; the side draws the
eye at a 3/4 slant).
- Body: a matte, painterly, near-black body (10,3,3) in three parts:
  - a tall pointed upper spire with slightly uneven edges;
  - a round disc / orb at mid-height;
  - a short rounded droplet extension below.
- Eye: a single large EYE on the disc's front: an off-white ring
  (251,242,233) around a dark pupil (23,10,10) with a rough hand-drawn
  edge. The back of the disc is plain black.
- Rim glow: a subtle deep-red (124,7,7) outer rim glow / outline. Build it
  as a faint emissive rim region, and report how.
- Palette: 10,3,3 / 48,45,44 / 124,7,7 / 251,242,233 / 23,10,10.
- Deviations to report: the side view shows the eye ring as a narrow oval
  (3/4); the build follows the front view, so in true side view the eye is
  near edge-on.

EMBER EYE ("ember_eye", ~7.0-7.4 u; orthos FRONT + BACK; the side is 3/4).
- Orb: a dark-red orb (115,33,33) with a deep-maroon (85,22,21) rim.
- Eye: a FRONT EYE: a white (244,243,243) inner ring with a red
  flame-shaped pupil (250,8,30). The back is a plain inset disc.
- Upper flame: a large, tall, wavy flame-like petal rising from the top,
  bright red with a soft-pink (253,165,166) highlight, with a tapered tip.
- Side flames: two smaller curved side flame-petals, left and right, each
  with a pink highlight.
- Lower ribbon: three hanging petal strips with tattered, cloth-like edges.
- Stem: a tapered dark-red lower stem / tail with a pointed tip.
- Palette (sheet hex): FF0000 / 7A1F1F / FFB3B3 / FFFFFF / 531212.
  Sampled: 250,8,30 / 115,33,33 / 253,165,166 / 244,243,243 / 85,22,21.
- Deviations to report: the back-view flame is drawn taller than the
  front-view one (tip at y 68 vs 114); reconcile it and say how.

## 2026-10-10 — NEW UNIT: Ember Stump (artist sheet)

Artist (verbatim): "next one".
Sheet (clipboard): design/reference/ember_stump-character-sheet.png
(intake --cuts 460,860; orthos BACK + SIDE; the front is slightly turned,
with the face and the fire offset to image-left).
Tier REGULAR [8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet
scale.

EMBER STUMP ("ember_stump", ~6.3 u).
- Body: an ARCHED, curving tree-STUMP trunk of rough, charred bark
  (79,68,60 brown-grey, 24,23,23 char, 193,187,179 ash-pale streaks), with
  a flared, root-split base on the floor.
- Mask: a JAGGED WOODEN MASK face, a flat slab with a ragged lower edge
  hanging at the trunk's top front.
  - Two GLOWING RED angular eyes (236,33,18).
  - A jagged zig-zag-toothed mouth.
  - FIRE IN THE MOUTH: a bold orange-red flame jet (253,121,29 / 236,33,18)
    streaming out forward and to the side.
- Crest: a SOOT CREST on top, a black ash plume of rough, wild,
  feather-like shapes (24,23,23 / 77,77,77).
- Arms: two BROKEN BRANCH ARMS, thin branches from the trunk ending in
  big upright split slabs of bark with jagged edges.
- Palette: 24,23,23 / 77,77,77 / 79,68,60 / 236,33,18 / 253,121,29 /
  193,187,179.
- Glow (drawn on the sheet): the eyes and the fire.
- Deviations to report: the fire is a static flame-jet mesh (no
  particles), and the bark texture is regions + the normal map.

## 2026-10-10 — NEW UNITS x3: Shield Rodent, Storm Vessel, Volcano Turtle (artist sheets)

Artist (verbatim): "next one" (each).
Sheets: design/reference/{shield_rodent,storm_vessel}-character-sheet.png
(clipboard) and volcano_turtle-character-sheet.webp (attachment).
Tier REGULAR [8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet
scale.

SHIELD RODENT ("shield_rodent", ~3 u; ALL views drawn from slightly above,
the back almost top-down; orthos FRONT + SIDE; back floor pinned to its feet
at y 610).
- Head: a small steel rodent head (light steel 189,184,181), with large
  ears with a warm-taupe inner (121,111,107), simple expressive black
  eyes, and a metal snout with two teeth.
- Shell: a flat, round SHIELD-SHELL body.
  - Top plate: a dark brushed steel disc (61,64,68 / 90,94,100).
  - A raised CENTRE BOSS.
  - A light-steel rim band with RIVETS all round.
  - Light scratches.
- Feet: tiny, simple feet, sturdy and low.
- Tail: a LONG, POINTED, faceted metal tail rising back and up.
- Palette: 189,184,181 / 129,132,138 / 90,94,100 / 61,64,68 / 121,111,107.
  Metallic, but flat-shaded per the house rule.

STORM VESSEL ("storm_vessel", ~6.6 u; orthos FRONT + BACK; the side is 3/4;
axes pinned to the orb: front 255 / back 668 / side 1085).
- Body: a big glossy vessel-blue (156,205,241) ORB body with a short open
  neck rim on top, from which glowing energy filaments spray.
- Head: a FLOATING HEAD above the neck (detached), a blue visor band with
  two horn tips over a white (247,247,247) half-dome face with two eyes.
  The eyes face forward; the back of the dome is plain white.
- Core: a GLOWING CORE set in the front centre of the body, an orange
  (254,102,2 / 254,175,20) energy ball with filaments inside a segmented
  metal-grey (143,148,159) ring frame.
- Arms: two FLOATING ARM PIECES, detached at the sides. Each is a blue band
  ring around a white dome "hand", with glowing filament bursts between it
  and the body.
- Tail: a JAGGED LIGHTNING TAIL attached under the body, a bold glowing
  orange / yellow-orange zig-zag bolt with thin spark crackles.
- Glow (drawn on the sheet): the core, the filaments, the neck spray and
  the lightning tail.
- Floats: the tail tip is the lowest point (min z 0); the idle is a hover.
- Deviations to report:
  - The detached pieces (head, arms) are separate closed parts on their own
    bones.
  - The thin spark crackles are simplified to a few thin glowing strands.

VOLCANO TURTLE ("volcano_turtle", ~6.8 u incl. flame; orthos FRONT + BACK;
the side is 3/4; floor pinned to the feet at y 808, under the drop shadow).
- Body: an upright, bipedal, chunky turtle of dark volcanic rock (61,57,58 /
  94,77,71), with a tan plastron belly (170,137,116).
- Head: a simple cute face with glowing orange eyes (254,143,58), a rocky
  brow (dark angular rock blocks) and a VOLCANO CRATER on the head. The
  crater has a glowing lava rim dripping down, and a constant flame rising
  from it (254,143,58 / 254,192,83) with a few small flame flicks.
- Arms: chunky cylindrical upper arms with big rock shoulder plates and
  OVERSIZED FISTS, crossed by LAVA CRACKS (glow).
- Shell: a TURTLE SHELL on the back, large rock plates separated by glowing
  lava cracks, with a tan rim.
- Tail: a short, stubby, pointed tail.
- Legs: stubby legs with toe claws.
- Palette: 61,57,58 / 94,77,71 / 170,137,116 / 136,58,47 / 254,143,58 /
  254,192,83.
- Glow (drawn on the sheet): the eyes, the lava cracks, the crater rim and
  the flame.
- Deviations to report: the flame is a static flame mesh.

## 2026-10-10 — NEW UNITS x2: Goo Shield, Fogeist (artist sheets)

Artist (verbatim): "next one" (each) + "yes carry on goo shield, I have
given you the references to intake, build them out into blends and after
all are done I will give feedback".
Sheets (clipboard): design/reference/goo_shield-character-sheet.png and
fogeist-character-sheet.png. Tier REGULAR [8000, 22000]. Idle + walk are
PLACEHOLDER. Natural sheet scale.

GOO SHIELD ("goo_shield", ~5.3 u).
- Intake: --bg-tol 12, because the near-black armour is close to the
  background (--bg-tol 8 crashed sheet_intake label_blobs with an
  IndexError; that is a kit defect, logged). Also --cuts 410,783 and side
  axis 1190.
- The SIDE view faces image-RIGHT: the eye is on the right and the goo
  trails left. goo_shield_render.py's ortho_side camera is flipped to -X
  for it, as for soot_puff.
- Body: a translucent, glossy lime GOO body (242,253,224 / 210,246,160,
  with a bright 147,250,11 rim). It is a tall slug neck rising from a wide
  lobed puddle base, which trails far back in the side view.
- Helmet: near-black, faceted rock HEAD ARMOUR (38,38,37 / 86,86,84) caps
  the head, with a V point between the eyes, hanging down at the sides.
- Eyes: two oval eyes, a pale ring around a bright-green iris.
- Shell plate: a big faceted near-black rock BODY ARMOUR plate (the shield)
  sits on the back / side (the back view covers the whole back; the side
  view is a large slab).
- Palette: 242,253,224 / 210,246,160 / 147,250,11 / 38,38,37 / 86,86,84 /
  133,140,124.

FOGEIST ("fogeist").
- Intake: the sheet is laid out in two rows (SIDE on top, FRONT + BACK
  below), so a single-row measurement copy was composed at the same pixel
  scale on a common floor line: design/reference/fogeist-character-sheet-
  row.png. Crops from the original: front (11,566,567,882) -> (70,174);
  back (573,567,1181,879) -> (696,178); side (99,120,1182,536) -> (1374,74).
  The intake ran on it (--cuts 660,1340; axes front 309 / back 823 / side
  1535). The original stays the art reference.
- Scale: the SIDE view is drawn ~1.3x larger than the front / back (head
  and eye sizes), and its mist crest rises higher (3.95 u vs 2.95 u).
  Reconcile them (the front / back fix the scale) and report how.
- Body: a low, reclining ghost made of soft, layered, cloud-like fog. A
  hooded rounded head with two simple dark eyes (~55,66,82) floating in the
  mist, a neck / chest rising from a puddled body.
- Crest: a LARGE flowing MIST CREST drifting back from the head.
- Tail: a long, tapering TRAILING TAIL of mist breaking into wisps.
- Material: semi-transparent mist (the firefly alpha path) over an opaque
  core.
- Palette (chips): 227,227,232 / 190,196,208 / 144,157,177 / 85,101,122 /
  55,66,82.
- Deviations to report: the wisps are closed lobed sheets / blobs, not
  volumetrics.

## 2026-10-10 — Goo Shield v1 verdict (artist verbatim + 2 markups)

"for the goo one  can we get rid of the sides here and instead extend the
middle down more and thicker there from a side angle view"
Markups:
- design/reference/goo_shield-v1-sideflaps-annotation.png: red outlines
  round BOTH helmet side flaps, the hood sides hanging down beside the eyes.
- goo_shield-v1-centre-annotation.png: a red outline of the centre V point
  extended down as a long rounded tab between the eyes, to about the
  eyes' lower edge.
Binding reading:
1. REMOVE the two helmet side flaps.
2. EXTEND the helmet's centre V point downward between the eyes, as a
   rounded-bottom tab reaching about the eyes' lower edge (per the markup),
   with its upper part flaring wider into the helmet as drawn.
3. Make that centre tab THICKER in depth, so it reads as a substantial
   nose-guard from a side angle, not a thin plate.

## 2026-10-10 — Shield Rodent v1 verdict (artist verbatim + markup)

"for the mouse the tail potion should be moved further back/down"
Markup: design/reference/shield_rodent-v1-tail-annotation.png (a red
outline of the tail rising from the shield's back rim edge, not from the
plate behind the boss).
Binding reading: move the tail root back and down onto the rear rim band.
TAIL_ROOT_R 0.65 -> 1.30 (the root sits on the rim top); same angle and
length.

## 2026-10-10 — Leaf Bird v1 verdict (artist verbatim + markup)

"for the bird  we should get rid of the crist and slim the bird down so its
more leaf likle"
Markup: design/reference/leaf_bird-v1-crest-annotation.png (a red X over
the back crest leaf).
Binding reading:
1. REMOVE the back crest leaf (part, bone, regions, clip keys).
2. SLIM the body so it reads as a LEAF: thinner across (flatter, narrower
   cross-section) and leaf-shaped, tapering to pointed ends, rather than a
   round plump bird body. Keep the head / eyes / beak readable, and keep the
   wings, the tail and the belly vein bands.

## 2026-10-10 — Ember Stump v1 verdict (artist verbatim + reference still)

"here he should be symmetical and not leaning to one side"
Reference: design/reference/ember_stump-v1-lean-annotation.png (the v1 back
still: the trunk's S-curve leans to one side, and the flame pokes out on
one side).
Binding reading:
1. The trunk is SYMMETRIC left-right, with no sideways lean or S-curve in
   the front / back views. The trunk axis is straight up the centre line;
   the side-view forward arch stays.
2. The arms / slabs are mirrored about the centre line (they already are).
3. Flame (orchestrator reading, reported): aim the fire breath straight
   forward on the centre line (no sideways azimuth), so the unit stays
   symmetric and the flame no longer pokes out past the trunk in the back
   view.

## 2026-10-10 — Crystal Canine v1 verdict (artist verbatim + 3 images)

"here we dont see symmetry on the model either for the legs, the art poses
them but our model should have symmetry and then we can pose
the chest hair is too lumpy and not fur enough and doesnt extend down
enough, we also lost fur detail on the legs, and the ears should go in on
the inner part of ear, like a canines ear, the head gem should be more flat
and level with the head"
Images:
- design/reference/crystal_canine-v1-walk-annotation.png (a walk frame);
- crystal_canine-v1-chest-annotation.png (a red mark at the chest-fur
  bottom between the front legs, where it should extend down to);
- crystal_canine-v1-face-reference.png (the sheet's face crop: a fluffy
  cheek / chest fur, a flat gem flush on the forehead, a cupped inner ear).
Binding reading (and a NEW HOUSE RULE, see below):
1. REST POSE SYMMETRIC: the four legs are mirror-symmetric (no hind / front
   stagger, equal spacing). The art's staggered pose is a pose, applied
   later by animation, not built into the model.
2. CHEST FUR: less lumpy, more FUR. Pointed fur locks / tufts (flame-like
   fur strands, as the sheet draws), not round lumps. Extend it further
   DOWN the chest, to between the front legs per the markup.
3. LEG FUR DETAIL: restore fur detail on the legs (tufts at the elbows /
   back of the forelegs, haunch fur, as the sheet draws), clean, not
   noisy.
4. EARS: the inner part goes IN, cupped / hollow like a canine ear (a
   concave inner face with the blush inner region), not a flat front face.
5. HEAD GEM: flatter and level with the head, a low faceted inlay flush
   with the forehead, not a raised pyramid.
NEW HOUSE RULE (artist, 2026-10-10): models are built SYMMETRIC in rest
pose even when the sheet draws an asymmetric pose (staggered legs, swung
tail, tilted parts). Posing comes after, in the rig / clips.

## 2026-10-10 — Goo Shield v2 verdict (artist verbatim + markup)

"for goo one more edit have the rock more parallel with the goo and more
craggly"
Markup: design/reference/goo_shield-v2-helmet-annotation.png. A red outline
of the wanted helmet: the sides come down hugging the goo column (not the
wide flared mushroom brim), then sweep in along the brow over the eyes to
the centre, where the nose-tab hangs down a little lower, with a rounded
bottom.
Binding reading:
1. The helmet's lower edge / sides run PARALLEL to the goo surface. Shrink
   the brim flare so the helmet hugs the head column with a thin, even
   overhang, instead of a wide mushroom cap.
2. The brow line sweeps in over the eyes to the centre tab, as drawn. The
   tab hangs slightly lower, with its rounded bottom, per the markup.
3. The rock reads MORE CRAGGY: rougher, more irregular faceting, with
   chipped / jagged edges along the lower rim, not smooth broad facets.

## 2026-10-10 — Ember Stump v2 note (artist verbatim)

"the fire out of his mouth should be an animation that has it on and off"
Reference: design/reference/ember_stump-v2-fire-reference.png (a v2
three-quarter still with the fire jet).
Binding reading:
- The fire breath is ANIMATED on / off, not a permanent part of the
  silhouette.
- rigkit.ALLOWED_CLIP_NAMES has no attack / breath name (shared kit,
  read-only), so the on / off cycle lives in the idle clip: fire off,
  then a burst that grows out of the mouth, holds, and dies back to
  nothing (flame bone scale 0 <-> 1, plus a mouth-open accent if
  cheap).
- walk: fire off throughout.
- The rest pose keeps the fire ON (so the model shows it for review and
  glb static previews); the clips key it off.
- A dedicated fire-breath clip would need a new allowed clip name in the
  shared rigkit (offered to the artist).

## 2026-10-11 — NEW UNITS x6: Lava Komodo, Spectormn, Halo Sentinel, Eclipse Wolf, Gemwing Dragon, Mystic Snow Spirit (artist sheets)

Artist (verbatim): "lets start intaking work again i will paste in
references" (then pasted Holly Snowman, Lava Komodo, Spectormn, Halo
Sentinel, Mystic Snow Spirit, Eclipse Wolf, Gemwing Dragon).
- Holly Snowman: NOT captured. The clipboard never held it, so it is
  waiting for a re-copy.
Sheets: design/reference/<unit>-character-sheet.{webp,png} (Lava Komodo
was an attachment; the rest came off the clipboard into incoming/). Tier
REGULAR [8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet scale.
HOUSE RULE (artist, 2026-10-10): the rest pose is SYMMETRIC. Any pose the
sheet draws (a swung tail, staggered legs) is built mirror-symmetric and
reported.

LAVA KOMODO ("lava_komodo"; orthos FRONT + SIDE; the side faces image-LEFT;
--cuts 305,612).
- Body: a SITTING komodo lizard of broken volcanic ROCK PLATES (67,58,57 /
  148,84,69 / 163,72,57), separated by glowing MAGMA CRACKS (254,115,58 /
  255,190,69).
- Head: a komodo head with rugged stone plates and an expressive orange
  eye.
- Flame crest: a FLAME CREST rising from the head and running down the
  neck.
- Chest / underside: glowing molten orange.
- MELTING LOWER BODY: the limbs merge into lava and drip, pooling into a
  glowing lava puddle on the floor.
- Tail: a long, tapered tail with rock plates, a glowing underside and lava
  drips. The sheet curls it to one side; it is built straight back (house
  rule).
- Glow: cracks, chest, crest, drips, pool.
- Accent: 167,145,125.

SPECTORMN ("spectormn", ~6.7 u; orthos FRONT + BACK; the side is 3/4;
axes front 232 / back 656 / side 1020).
- Body: a hooded SHEET-LIKE CLOTH ghost: white (247,241,237) with
  off-white folds (206,198,199) and a pointed hood.
- Eyes: two RED GLOWING OVAL EYES sunken into the cloth (216,3,17).
- Chest: three vertical glowing red CHEST SLITS.
- Arms: long sleeved arms hanging down.
- Chains: RED GLOWING CHAIN loops hang from both wrists (dark-grey 38,39,43
  wrist shackles; red glowing links).
- Lower half: TATTERED, fading into a semi-transparent purple / pink-red
  MIST (116,84,111 / 185,82,94) toward the floor. It floats.
- Glow: the eyes, slits and chains.
- Mist: alpha path, BLENDED (no dither grain), as in fogeist.

HALO SENTINEL ("halo_sentinel", ~6.1 u; three views; --cuts 545,1030;
axes front 288 / back 752 / side 1150; the side faces image-LEFT).
- Body: a magenta (138,20,67) smooth rounded body. A round head with a
  single VERTICAL EYE (cream 234,191,204 almond, orange 250,105,16
  pupil).
- Arms: broad, rounded, WING-LIKE ARM extensions sweeping out to the
  sides.
- Belly eye: a second vertical eye emblem on the lower belly (pink 209,80,136
  rings).
- Halo: a FLOATING ORANGE HALO ring above the head, with two small HORN
  arrow tips curving down at its sides.
- Lower body: translucent, jelly-like, crystalline-watery BLUE (101,158,192
  / 32,87,131) pillar shapes.
  - Front / side: two leg pillars.
  - Back: one wide skirt cone. Reconcile them, and report how.
  - Watery, ribbed texture (as regions + normal); alpha BLENDED.
- Floats: the shadow is drawn below.

ECLIPSE WOLF ("eclipse_wolf", ~6.4 u; three views; --cuts 470,860; axes
front 218 / back 652 / side 1010; floors at the paws; the side faces
image-LEFT).
- Body: a standing four-legged wolf with near-black fur (35,30,35), pointed
  ears with light inner fur, narrow expressive eyes and a sharp TOOTHY
  GRIN.
- Fleece mantle: a WOOLLY FLEECE MANTLE (122,109,109 / 229,221,217) of
  rounded curl clumps runs from the chest along the body into a fluffy
  wool TAIL. The sheet swings the tail to one side; it is built
  symmetric (house rule).
- Horns: two LARGE SYMMETRICAL CRESCENT HORNS behind the head (dark purple
  56,28,58), with a GLOWING MAGENTA (253,49,253) inner edge, plus a
  floating dark ORB with a magenta glow ring between them.
- Leg bands: glowing magenta energy BANDS around the four lower legs.
- Glow: the horn inner edges, the orb ring and the leg bands.

GEMWING DRAGON ("gemwing_dragon"; orthos FRONT + SIDE; floors at the feet:
front 764, back 760, side 772; axes 174 / 500 / 900; the side faces
image-LEFT; no palette chips detected; sample them).
- Body: a four-legged dragon made entirely of faceted, prismatic, slightly
  translucent CRYSTAL (pearl white, periwinkle, indigo, blue, violet,
  peach-orange gradients per facet).
- Head: an angular reptilian head with a crown of gemstone spikes and
  reptilian eyes.
- Wings: CRYSTALLINE WINGS of layered crystal blades fanning up and back.
- Spikes: gemstone spikes along the neck and back; crystal plates over the
  whole body.
- Tail: a long faceted crystal tail. The sheet swings / curls it; it is
  built symmetric (straight back), per the house rule.
- Glow: none drawn beyond the prismatic sheen. Facets are toned by light
  direction, as in chest_monster / crystal_canine.

MYSTIC SNOW SPIRIT ("mystic_snow_spirit", ~7.0 u; three views; --cuts
485,880; the side faces image-LEFT).
- Body: a snowman: a round snow head, a smaller middle ball, and a large
  conical snow-mound skirt base with a chunky snow-pile rim (237,238,242 /
  193,215,237).
- Face: oval eyes with blue irises, a carrot-like SNOW NOSE, a small "o"
  mouth.
- Horns: branch-like TWIG HORNS with snow accents (symmetrical).
- Arms: long branch / wooden ARMS with twig fingers, snow on top, hanging
  down.
- Snowballs: FIVE FLOATING SNOWBALLS orbiting around the head / shoulders,
  each consumed in a stylised mystical blue / cyan / violet FLAME (92,173,214
  / 139,93,253 / 46,67,108), glowing.
- Glow: the flames, plus a faint blue under-glow at the base.
- Asymmetry: the snowballs are placed asymmetrically on the sheet. They
  are built symmetric (2 + 2 + 1 top), per the house rule.

## 2026-10-11 — Gemwing Dragon pre-build direction (artist verbatim)

"for gemwing dragon tone the crystalness of the body and save the most color
for the wings and spikes around it"
Binding reading:
- BODY (head, neck, torso, legs, tail): toned-down crystal. Mostly the pale
  pearl / soft periwinkle base, with gentle, low-contrast facet shading and
  only sparse, muted colour accents. Not the full prismatic rainbow of
  facets.
- WINGS + SPIKES (the crown spikes, the neck / back / tail spikes): carry
  the saturated prismatic colour (blue, violet, indigo, peach-orange
  gradients) and the strongest facet contrast.

## 2026-10-11 — Halo Sentinel v1 verdict (artist verbatim + reference still)

"for this creature have the skirt connect in the back and curve around the
front a little more"
Reference: design/reference/halo_sentinel-v1-skirt-reference.png (the v1
front + threequarter stills: two separate blue pillars).
Binding reading:
- The blue lower body becomes ONE continuous SKIRT, joined across the back
  (no V-gap; it reads as the back view's single cone skirt).
- The skirt wraps further round the sides toward the front, curving in so
  its front edges come nearer the belly lobe. It stays OPEN at the front
  centre: the belly lobe / emblem still shows through, and the front view
  still reads as two panels flanking the belly.
- Keep: the flare toward the bottom, the translucent jelly material and
  stripes, symmetry, and the floating min z 0 at the hem.

## 2026-10-11 — NEW UNIT: Holly Snowman (artist sheet)

Artist: pasted again (the earlier paste never reached the clipboard).
Sheet: design/reference/holly_snowman-character-sheet.png (intake: --cuts
470,880; axes front 250 / back 668 / side 1010; the side faces image-LEFT).
Tier REGULAR [8000, 22000]. Idle + walk are PLACEHOLDER. Natural sheet
scale. HOUSE RULE: symmetric rest pose.

HOLLY SNOWMAN ("holly_snowman", ~5.9 u).
- Body: a FLUFFY, icy-blue snowman (192,210,253 base / 133,167,252 shadow /
  227,240,254 highlight). A big round head over a pear-shaped body, with
  scalloped, cloud-like, fluffy outlines and subtle texture.
- Feet: two small rounded snow FEET.
- Face: two simple black oval eyes (34,36,40), with a soft blue blush
  shading below them.
- Leaf ears: two dark-green (16,47,29) LEAF EARS on the head, plant-like
  leaves with a slight curve and a lighter rim / midrib.
- Arms: PLANT ARMS of leafy fronds (layered pointed green leaves) at the
  shoulders, holding RED BERRY CLUSTERS (178,37,38) with highlights.
- Buttons: three red berry BUTTONS down the front.
- No glow.

## 2026-10-11 — Gemwing Dragon v1 verdict (artist verbatim + 3 markups)

"lets make it more a wing pattern / lets also make it wider on the bottom,
shown in second picture / and the head spikes more in the shape of a
crown"
Markups:
- design/reference/gemwing_dragon-v1-wing-annotation.png: a red outline of
  a WING silhouette over the side view. A swept, bird / dragon wing shape:
  a leading edge rising from the shoulder and arcing back to a high wingtip,
  with a stepped / scalloped trailing edge of 2-3 layered feather points
  sweeping back, rather than the v1 upright fan of separate spikes.
- gemwing_dragon-v1-belly-annotation.png: a red line under the body from
  the chest down round the belly to the hind leg / tail root. The
  underside / lower body should be WIDER and deeper (a fuller chest and
  belly hanging lower), not the slim v1 underline.
- gemwing_dragon-v1-crown-annotation.png: a red outline of a CROWN shape on
  the head: the spikes arranged as a regal crown, a ring / band of
  upright points (a taller centre point, symmetric points stepping down to
  the sides) sitting on the head, rather than the v1 swept-back mohawk.
Binding reading: (1) wings in a wing pattern per the markup; (2) a deeper,
wider lower body; (3) the head spikes as a crown. The colour direction
from 2026-10-11 still holds (a toned body; the colour on the wings and
spikes).

## 2026-10-11 — Eclipse Wolf v1 note (artist verbatim)

"can we make the inner mouth purple like the glowing color to contrast with
the teeth"
Reference: design/reference/eclipse_wolf-v1-mouth-reference.png.
Applied: palette region mouth 11,7,10 -> 253,49,253 (the glow magenta), as
colour only (not emissive; the glow set stays horns / orb ring / bands).
Follow-up (artist verbatim): "instead of just the painted look can we push
the mouth in and have the teeth carved out"
Reference: design/reference/eclipse_wolf-v1-mouth-depth-reference.png (the
v1.1 face close-up: flat decal teeth on purple).
Binding reading: real DEPTH. The mouth is a recessed cavity pushed into the
head (purple interior walls and floor). The teeth are separate carved /
modelled solid points (upper row hanging down, lower row pointing up) that
stand proud in the opening, with real gaps between them showing the
recessed purple. No painted decals.

## 2026-10-11 — Gemwing Dragon v2 verdict (artist verbatim + markup + the sheet)

"can the crown component be one merged piece rathe than each being
separate / same for the wings, more one piece like the original reference,
lets try to get a close look to the refeence and add the ggems on the chest
piece that extend to the start of the front legs like the eference like an
armor / the whole body should also look more gemotric like a body made of
crystals"
Markup: design/reference/gemwing_dragon-v2-crown-annotation.png (red lines
under the crown: the points should share one merged base band on the
head).
Binding reading:
1. CROWN: ONE merged piece. A faceted crystal band / base wrapping the head
   top with the points growing out of it as a single connected crystal
   form, not 9 separate sticks.
2. WINGS: each wing is ONE connected crystal piece, as the sheet draws it:
   a solid faceted wing mass whose layered blades are fused together and
   read as one wing, not separate floating blades. Get close to the
   reference wing shape (the sheet's side view: a big fan of fused blades
   sweeping up-back), keeping the v2 wing-arm silhouette where it agrees.
3. CHEST ARMOUR: add the sheet's chest crystal plates. A V / chevron of
   overlapping gem plates on the chest, running down onto the shoulders
   and the start of the front legs like armour (front view: the stacked
   V-plates; side view: the plates over the chest and forearm tops).
4. BODY GEOMETRY: the whole body reads as made of crystals. Real faceted,
   planar geometry (a low-poly crystal surface with crisp facet planes and
   shard-like plates on the neck, legs and tail), not a smooth body with
   painted facet tones.
Colour (orchestrator reading, reported): keep the 2026-10-11 balance. The
body facets stay pale pearl / periwinkle; the chest armour gems, crown,
spikes and wings carry the saturated prismatic colour.

## 2026-10-11 — Gemwing Dragon v3 verdict (artist verbatim + 2 markups)

"the chest should be a piece like this / the crown should extend like this /
right now our creature looks like a horse rather than a dragon"
Markups:
- design/reference/gemwing_dragon-v3-chest-annotation.png: a red outline on
  the front view of ONE chest piece. A spiky starburst / flame-shaped
  plate (pointed spikes radiating out round its edge, a pointed bottom),
  centred on the upper chest, replacing the scattered separate gem plates.
- gemwing_dragon-v3-snout-annotation.png: a red outline on the head side
  view. The crown band EXTENDS FORWARD from the crown down along the top
  of the snout to the nose tip, ending in a pointed / hooked tip just
  above the nostril (a crystal snout ridge / armour plate over the
  muzzle).
Binding reading:
1. CHEST: replace the 18 separate plates with ONE merged chest-armour piece
   in the outlined starburst shape: a central faceted gem plate with
   radiating pointed spikes round its edge, pointed at the bottom (the
   sheet's chest chevron). One closed part, saturated prismatic colour.
2. CROWN EXTENSION: the crown continues forward as one merged crystal
   ridge along the top of the head and snout to the nose, with a pointed
   tip over the nostrils. It stays part of the one crown piece.
3. DRAGON, NOT HORSE: re-proportion toward the sheet's dragon:
   - a longer, lower, angular reptilian head (flatter skull, longer
     tapering snout, a strong brow ridge, an angular jaw line);
   - a longer S-curved neck carried forward / low;
   - a lower, longer body;
   - legs with dragon / reptile structure (bent, clawed, heavier forearms,
     reverse-jointed hind legs), not straight horse legs;
   - a longer, heavier tail base.
   Look at the sheet's side view as the master.

## 2026-10-11 — Gemwing Dragon v5 verdict (artist verbatim)

"why does the dragon look so low poly, and its snout and mouth are so
small, its wings look like a mess" -> (after the explanation + the offer of
the boss budget) "yes do it".
Binding reading:
1. TIER: Gemwing moves to the BOSS tier [20000, 30000] (artist approved).
   The extra budget goes to the body.
2. BODY: smaller, crisper crystal facets (~0.2-0.3 u, the sheet's shard
   size) instead of the 0.45 u planar cells, so it reads as a fine
   crystal mosaic, not chunky low-poly.
3. SNOUT / MOUTH: restore a long wedge dragon snout with a proper jaw and a
   longer mouth line, as the sheet's head panel draws (the v5 shortening
   was an orchestrator over-correction).
4. WINGS: a clean fan of long straight layered blades, each with a few
   large facets and one root-to-tip gradient, tips forming an even
   stepped edge; no per-facet colour speckle, no streak noise, no tips
   poking out of a fused blob.

## 2026-10-11 — Artist sizes for the new units (verbatim)

"crystalline creatue 5 feet, eclipse wolf 4 ft, eye totem, 3 and half feet,
winged cat 3 feet, phienix hatchling 3 feet, chest monster 4 feet, storm
vessel 6 feet 3 inch, soot puff 2 ft, holly snowman 5 ft, mystic snowman
5ft 8, ember stump 8ft, goo shield 5ft 5, volvano turtle 7 ft, gemwing
dragon 13ft, leaf bird 1ft and its orientation needs to be fixed, right
now thats a top view so height may be wrong, ember eye 4ft 5 in, halo
sentinel 6ft 3 inch, foggeist 4 feet 8 inches, lava komodo 4 ft 3 inches"
Applied: design/size-table.json target_height_m for 19 units (crystalline
creature = crystal_canine; mystic snowman = mystic_snow_spirit).
Still unsized: petal_cobra, shield_rodent, spectormn (asked).
Leaf Bird: re-orient (the rest pose reads as a top view from the front),
then size it at 1 ft.
Follow-ups (artist verbatim): "leaf bird should be half a foot in height and
a foot long" and "if i did not give height then those heights ae fine".
Applied: leaf_bird target 0.1524 m (6 in) with a 1 ft (0.3048 m) length, so
the model must be re-proportioned to L/H ~2 (v3 is 4.45). petal_cobra,
shield_rodent and spectormn keep their lineup placeholder heights.

## 2026-10-11 — Pixal3D trial verdict (artist verbatim)

"all of the work it did looks better than what we did, except for the
called out issues of model interpertation etc, but it looks 'better'"
Binding reading: for NEW units, move toward using the Pixal3D 3-view mesh
as the BASE (option i, "generate + finish"), with our system fixing what it
gets wrong:
- symmetric rest pose (mirror-average, not bisect);
- crisp crystal / blade / horn / crown parts swapped in for its fused shard
  noise;
- palette-snapped colours (no drift);
- clean topology at the tier budget;
- rig + clips;
- sheet-score gates.
The reference-only lane (ii) in progress is the prerequisite (the shared
frame fix, the proxy tool, smart intake, the numeric gate).

## 2026-10-11 — Generate-and-finish prototype verdict (artist verbatim)

"for the snowman i like column 3 / for gem dragon column 2 is better,
because it seems in trying to fix the symmetry and make changes we broke
some things, like the mouth"
Reading:
- Holly Snowman: the PROXY-FINISHED version (column 3) is preferred over
  the raw proxy and the hand build.
- Gemwing Dragon: the RAW Pixal3D proxy (column 2) is preferred. Heavy
  finishing (mirror-averaging, cuts, part overrides) broke detail such as
  the mouth / face. Lesson: the finish stage needs a LIGHT mode that keeps
  the proxy's geometry and face detail as generated, and only does what is
  needed to ship: watertight / clean, budget decimation with a texture
  bake, floor + scale, rig, clips, gates. Symmetrising / overrides become
  opt-in per part, never on the head / face.

## 2026-10-12 — NEW UNIT: Sand Drill Worm (artist reference sheet)

Artist (verbatim): "here is the next one".
Sheet: design/reference/sand_drill_worm-character-sheet.png (pasted inline,
loaded off the clipboard; the original is in incoming/). Smart intake
(intake.json intake_notes) with --side-facing right: the side view's mouth
opens up and to image-RIGHT. Orthos FRONT + SIDE. Tier REGULAR [8000,
22000]. Idle + walk are PLACEHOLDER. Natural sheet scale.
FLOW (artist direction 2026-10-11): Pixal3D 3-view proxy as the BASE,
LIGHT finish (keep the generated geometry / texture / face), bone repose
only for posed parts, rig + clips + gates. Not a hand-written procedural
build.

SAND DRILL WORM ("sand_drill_worm", ~6.7 u).
- Body: an upright segmented sandworm, a bowling-pin silhouette.
  - A slender neck widening to a fat lower body, tapering to a pointed
    drill-tail tip that the unit stands on.
  - Ring segments in alternating light / mid sand bands (238,201,154 /
    201,155,106 / 173,123,76); tough leathery, weathered texture.
  - The lower tail carries dark (89,65,49) drill bands with sand edges.
- Mouth: a WIDE CIRCULAR MOUTH at the top.
  - A ring of sharp cream teeth (2-3 rows).
  - A dark fleshy red interior (116,62,58).
  - A sandy, tough rim.
- Drill arms: two thin DRILL ARMS from the neck sides. Each hangs as a long
  stalk into a spindle / drill bulb with spiral dark chitin striping and a
  pointed tip. The side view shows both arms hanging behind the body; the
  front / back show them out to the sides.
- Palette: 238,201,154 / 201,155,106 / 173,123,76 / 138,93,58 / 89,65,49 /
  116,62,58.
- No glow.

## 2026-10-12 — Crystal Canine (Pixal3D flow) verdict (artist verbatim)

"fo the cyrstal creature seems torso should be a bit longer, and the face
needs polishing, it has an extra nose and the eyes feel off, dont line up
and are too low"
Binding reading (applies to the Pixal3D-flow build, scratch):
1. TORSO a bit longer: lengthen the body between the shoulders and hips
   (a spine stretch via bones / a sectional scale), legs / head / mane
   unchanged.
2. FACE polish (an explicit artist ask, so a targeted face edit is allowed
   here):
   - remove the EXTRA NOSE (a second nose bump / dark spot);
   - the EYES must be level / mirror-matched and sit HIGHER, at the
     sheet's eye height relative to the head (front view head crop);
   - the rest of the face (muzzle, gem, ears) is kept as generated.

## 2026-10-12 — Sand Drill Worm v1 verdict (artist verbatim)

"yes only 2 arms, mouth dark red. teeth are fine"
Binding reading:
1. Remove the proxy's extra drill-arm pair (Pixal3D read the side view's
   offset drills as 4 arms). Keep ONE arm per side, matching the front /
   back views; mirror-matched.
2. Recolour the mouth INTERIOR (the throat / flesh inside the teeth ring)
   to the sheet's dark red (sheet interior ~63,28,24; chip 116,62,58 for
   the lighter flesh). The teeth and the sandy rim keep their colour.
3. The teeth stay as generated.

## 2026-10-12 — Sand Drill Worm v2 note (artist verbatim + markup)

"seems a disjoint on the arms here where there is leftove"
Markup: design/reference/sand_drill_worm-v2-arm-seam-annotation.png (a red
line along the arm stalk from the shoulder down: a seam / leftover where
the cut fore arm was joined to the kept aft arm).
Binding reading: clean up the cut. No leftover sliver, ridge or texture
seam of the removed arm along the kept arm's stalk. The kept arm reads as
one smooth continuous stalk from the shoulder socket down to the drill
bulb.

## 2026-10-12 — Crystal Canine v3 (Pixal3D flow) verdict (artist verbatim + 2 images)

"seems we have an extra black part here / the snout needs to extend
further out on the side view it looks bad and not like the ref"
Images:
- design/reference/crystal_canine-v3-dark-marks-annotation.png: red marks
  on two dark / black streaks above and outside each eye (at the
  brow / temple, between the eye and the ear base). Extra painted marks the
  sheet does not draw.
- crystal_canine-v3-snout-side-reference.png: the v3 side view. The muzzle
  is short and blunt; the sheet's side view has a longer, pointed fox
  snout.
Binding reading:
1. Remove the two extra dark marks at the brows / temples (repaint them as
   the surrounding fur).
2. Lengthen the snout forward in the side view to match the sheet's side
   profile: a longer, tapered fox muzzle with the nose at the tip. Nose,
   mouth line and eyes stay; the muzzle geometry is extended (an explicit
   artist ask, so this targeted face reshape is allowed).

## 2026-10-12 — Crystal Canine swapped + Eclipse Wolf held (artist verbatim)

"for cyrstalline canine lets swap, eclipse wolf ours looks cleaner, there
is too many smears and tears on the other, we can try to fix those and see
how it turns out"
- crystal_canine: REPLACED by the Pixal3D light-flow v4 (2-view proxy,
  light finish, symmetric leg repose, torso +7%, tail to the sheet side
  curve, face paint: extra nose removed + eyes levelled + brow marks
  erased, muzzle pulled to the sheet's side length). Digest
  fe4539243ee1635f.
- eclipse_wolf: the HAND BUILD stays in the game. The Pixal3D version goes
  back for a smear / tear cleanup and a side-by-side re-judge.

## 2026-10-12 — NEW UNIT: Vulture Creature (artist reference sheets x2, mixed)

Artist (verbatim): "next item to intake" / "there are two here we want a mix
of them" / "so we want the black wings and more skull like shape, but
without the green aura". Answers to the mix questions: head = bone skull
helmet; feathers = black (spirit); glow = NONE; extras = skull pendant on
a cord only.
Sheets: design/reference/vulture_creature-spirit-sheet.png (base:
black wings, skull helmet) and vulture_creature-natural-sheet.png (natural
variant, NOT used except as context). The BUILD reference
vulture_creature-character-sheet.png = the spirit sheet with the green
aura / glow digitally removed (green-dominant pixels neutralised), so the
proxy doesn't bake green. Intake: --region 0,115,1200,845 --cuts 400,765
--side-facing left --ortho-views front,side (smart intake misread the dark
sheet). Tier REGULAR [8000, 22000]; idle / walk PLACEHOLDER. Pixal3D light
flow.

VULTURE CREATURE ("vulture_creature", ~6.8 u).
- Head: an upright vulture under a weathered BONE BIRD-SKULL HELMET.
  - A long hooked cream bone beak (223,204,183), two horn points on top,
    hollow dark eye sockets (NO green glow), strapped on with dark ties.
- Body: deep black / near-black ragged feathers (27,27,27 / 106,99,96 /
  74,77,69), with large draped WINGS hanging like a cloak to the floor,
  covering most of the body, and ragged tattered edges.
- Chest: a small bone SKULL PENDANT on a cord. The tattered cloth
  wrappings the spirit sheet draws are NOT wanted (only the pendant was
  picked); remove them if feasible and report.
- Feet: scaly grey-brown bird legs, three front toes + one rear, dark
  talons.
- No glow, no aura, no wisps.

## 2026-10-12 — Vulture Creature v1 verdict (artist verbatim + 1 image)
Artist (on the v1 finish panel, side view crop): "can we add green glow in
the eyes and then the skull necklace make it a green spectre glow"
Binding reading (supersedes "glow = NONE" for these two parts ONLY):
- Eyes: a green glow inside both eye sockets of the skull helmet (the spirit
  sheet's green, sampled from vulture_creature-spirit-sheet.png), emissive.
  The sockets stay hollow; the glow sits in them.
- Chest skull pendant: a green SPECTRE glow: the pendant skull glows green
  (emissive), ghostly. The cord stays dark.
- Everything else stays as v1: black feathers, cream bone helmet, no green
  on the body, no aura / wisps elsewhere. The other v1 decisions (skull tone,
  talons, cloth-strip shape) not answered: v1 kept as is.

## 2026-10-12 — Eclipse Wolf (Pixal3D version) mouth note (artist verbatim)
"go ahead and import crystalline creature for eclipse wolf we need to
correct the mouth to match ours, teeth and purple inside"
- crystalline creature = crystal_canine: v4 is already in Conquest
  (666d195, glb byte-identical to forge rigged/crystal_canine.glb).
- eclipse_wolf, the Pixal3D light-finish version (scratch, not promoted):
  the mouth is a pale painted grin. Binding reading: match the hand build's
  v1.2 mouth (entry "2026-10-11 — Eclipse Wolf v1 note"): a recessed mouth
  cavity with the purple interior (253,49,253, colour, not emissive) and
  separate solid pointed teeth (upper row down, lower row up) with gaps
  showing the purple. Reference render: renders/eclipse_wolf/eclipse_wolf_face.png
  (hand build). Rest of the Pixal3D face kept as generated (light touch).

## 2026-10-12 — Eclipse Wolf held + Vulture Creature v2.1 approved (artist verbatim)
"lets keep ours for now and add the vulture" / "vulture height 5 ft"
- eclipse_wolf: the HAND BUILD stays in the game. The Pixal3D v3 (carved
  mouth, purple interior, 15 solid teeth; sandbox digest d08472a6d58ffa95)
  stays in the session scratchpad, not promoted.
- vulture_creature v2.1 (digest 5b3aaa03830de189: green emissive eye cores,
  bone skull pendant with green-glowing sockets) APPROVED: commit and import
  into Conquest at 5 ft = 1.524 m (size-table target). Movement still open
  (N15): the idle + walk clips ship as PLACEHOLDERS.

## 2026-10-12 — Sand Drill Worm height + Conquest branch push (artist verbatim)
"I am not sure what effect the mipmaps will do, that height is fine, and whats
on the branch? can push"
- sand_drill_worm: target height = its measured 1.802 m (size-table row kept).
  Not imported yet; movement still open (N14).
- Conquest feat/forge-unit-import pushed to origin at ec87b06 (7 forge-import
  commits, 6ba132b..ec87b06).
- crystal_canine basecolor mipmaps: explained, pending the artist's call.

## 2026-10-16 — NEW UNITS x14: batch of artist reference sheets (artist verbatim)
Artist (verbatim): "prepare to intake more conquest references and create
blends for them" / "I am going to paste in many references" / "each is its
own character".
Common to all 14: sheets pasted inline, saved as
design/reference/<unit>-character-sheet.png (webp -> png, pixels unchanged);
smart intake; Pixal3D LIGHT flow (proxy_finish.py, "mode": "light": proxy
geometry + texture as generated, no symmetrise / repose / cuts unless a
region needs it, never on the head / face); tier REGULAR [8000, 22000];
idle + walk PLACEHOLDER clips; movement + in-game height asked (OPEN-QUESTIONS
N16-N29). Every side view faces image-left (meshgen 'left'). Glow ships ONLY
where the sheet draws it. Two sheets share the title EMBER REX: per "each is
its own character" they are two units, ember_rex (darker, ember-tipped
feathers) and ember_rex_2 (living-flame plumage).

ICEHORN MOOSE ("icehorn_moose", ~6.8 u). Intake --region 0,0,1190,850 (the
side box ran into the detail panels).
- Light-blue furred moose (181,206,234), a thick white volumetric NECK COAT /
  mane (234,232,231) distinct from the body fur, shaggy tufted legs, dark
  slate hooves (63,82,107), shadow 106,125,160.
- Head: rounded moose snout, solid dark eye, tall ears.
- Antlers: TRUE ICE, translucent crystalline, angular, internal cracks, a
  subtle frosty glow (icy blue 129,217,251).

FROST CALF ("frost_calf", ~6.6 u).
- Young deer/moose calf: light-blue soft short fur (196,216,234 /
  156,187,223), slight neck tuft, smooth body, long thin gangly legs, flared
  dark hooves (66,84,112 / 50,61,78), large dark eyes, rounded muzzle with a
  dark nose, tall upright ears, NO horns / antlers.
- Short upward fluffy tail tuft in darker blue (80,126,183).

FROST GIANT ("frost_giant", ~7.8 u).
- Tall gaunt faceted-ice humanoid (212,234,250 / 167,213,243 / 128,190,238),
  darker blue chevron seams (50,120,183).
- Head: angular ice MASK with two straight crystalline horns and narrow
  glowing eye slits (96,210,251); a long hanging ICICLE BEARD down to the
  chest, irregular lengths, semi-transparent.
- Very long arms with large clawed icicle hands reaching the knees; thin
  legs; ice plates and frosty edges along the arms / shoulders.

FROST GIANT CHILD ("frost_giant_child", ~6.7 u).
- Chunky low-poly faceted crystal child (233,233,232 / 177,210,243 /
  105,163,236 / 84,104,150, accent 128,144,249).
- Big angular head, two small emerging ice horns, large simple glowing pale
  eyes, minimal mouth; crystal growths from neck + shoulders; fingerless
  tapered crystal arms; sturdy wide crystal feet.

STORM BEAR ("storm_bear", ~6.8 u).
- Upright white-furred bear (230,221,218), a thick dark charcoal FUR RING
  around neck / head running down the back as a cape (69,71,86 /
  101,108,136).
- Translucent blue ICE CRYSTALS with lightning contained inside, integrated
  into the fur on chest, arms, legs (188,218,253 / 61,106,244; the sheet's
  6th chip, bright cyan, was not auto-found).
- Massive ice-crystal CLAWS on hands and feet, internal glow, NO external
  electric arcs. Glowing blue eyes.

SPIKE BEAK ("spike_beak", sheet 6, the small round one, ~3.9 u).
- Round ball-shaped blue bird (182,208,227 / 106,158,216 / 70,114,179 /
  54,67,97), small tucked wings, short feathered tail.
- Long straight yellow beak (236,206,129 / 221,176,89) that rises into a
  small forehead SPIKE; round expressive eyes (cream ring, dark pupil).
- Yellow bird legs, three front toes, small curved dark claws.
- Front-vs-side height -10 %: the forehead spike reads taller seen from the
  front; the side is the depth reference.

SPRING PIG ("spring_pig", ~6.1 u).
- Round pink pig (244,152,153 / 212,120,126, highlight 252,231,231), biped
  on two short sturdy legs with brown hoof feet (116,79,68 / 95,67,58).
- Ball body with a brown torso PANEL integrated into the body (no straps or
  rivets); stubby arms with brown tips.
- Head: round, long rectangular snout, upright pointed ears, a simple
  GOGGLE (round dark lens, brass rim 208,152,105, brown strap).
- Tail: a pink coiled SPRING tail from the lower back to the floor (soft,
  natural, no mechanical parts).

RAVEN SHADE ("raven_shade", ~6.3 u). Intake --cuts 443,804 (the matte
fused the views).
- Hunched raven assassin in black / charcoal feathers (29,29,31 / 75,73,81 /
  64,59,77), blade-like feather edges tipped steel-blue (97,102,127).
- Head: hooded feather crest, large hooked dark beak, narrow red eye
  (141,55,65), subtle scar marking.
- Layered feather CLOAK / tucked wings to the floor; a small diamond
  PENDANT clasp at the throat and on the back (tan 191,168,153).
- Large scaled dark talons, thick claws; layered blade-like tail feathers.

EMBER REX ("ember_rex", sheet 9, ~6.6 u).
- Tall phoenix-like raptor bird: dark brown-red body (70,52,54 / 127,45,46 /
  157,66,60), layered dark neck feathers, a lighter tan breast, a small
  glowing ember GEM on the chest.
- Phoenix flame crest, sharp yellow beak, flame-tipped feathers
  (248,127,74 / 253,197,109 / 235,219,206).
- Large razor-like wing feathers hanging to the floor, gradient brown/red
  to orange/yellow; long tail feathers. Raptor legs, scaled, curved talons.

EMBER REX 2 ("ember_rex_2", sheet 10, ~6.7 u). Intake on a split-helper
copy design/reference/ember_rex_2-character-sheet-split.png (the back
view's tail tip under the side view blacked out; the original is
untouched): --region 0,60,1195,850 --cuts 339,612 --floor 786/786/788
--axis back=578.
- Phoenix of living flame: high flame crest, flowing flame collar, cream
  scaled neck / breast (246,214,182), deep red to bright orange / yellow
  plumage (68,38,42 / 161,42,48 / 225,76,58 / 253,124,67 / 253,201,100).
- Large blade-like flame wing feathers; a LONG draconic flame tail trailing
  on the floor; raptor legs with glowing flame-lit cracks, curved talons.
- Glow: the flame plumage and the leg cracks are drawn self-lit.

IRON SCALE KNIGHT ("iron_scale_knight", ~6.8 u).
- Armoured pangolin knight: overlapping steel scale plates (245,233,221 /
  195,176,164 / 124,115,114) with gold trim and rivets (200,149,100), dark
  cloth underlayer (70,59,56 / 91,62,54), leather straps + buckles, a
  hanging tabard with a gold emblem.
- Head: pangolin helmet, elongated snout, narrow amber eye slit, a thin
  crest arc over the top.
- Long armoured scaled tail to the floor; heavy armoured legs, clawed feet.

SHADOWSCALE ("shadowscale", ~6.9 u).
- Assassin pangolin: dark gunmetal blade-like scale plates (21,21,21 /
  56,49,51 / 97,88,91), crimson cloth scarf / hood wrap and hanging strips
  (111,35,40), leather straps with rings and tassels (83,65,60).
- Head: pangolin helmet, elongated snout, red predatory eye, crest arc.
- Blade-scale arms, long flexible blade-scale tail, digitigrade armoured
  legs with curved talons.

AZURE SCALE MAGE ("azure_scale_mage", ~7.2 u).
- Pangolin mage: blade-like scales in a white-to-blue gradient (38,42,63 /
  54,72,130 / 117,141,203 / 185,198,230 / 234,222,213) flowing like a robe;
  dark scaled legs (110,105,111).
- Head: a SORCERER HAT made of scales, its tip curling forward, integrated
  into the head and neck scales; pangolin snout; narrow glowing blue eye.
- Embedded glowing blue ARCANE CRYSTALS on chest, arms and robe; long
  flowing scale tail; digitigrade legs with talons.

DRAKESCALE BEAST ("drakescale_beast", ~6.6 u).
- Quadruped pangolin-dragon: layered brown pangolin scales (43,36,36 /
  72,57,48 / 124,87,65) with lighter tips (188,146,115), a cream belly and
  throat (220,187,156).
- Head: pangolin-inspired draconic snout, horn-like scale protrusions,
  narrow orange eye; dorsal spines along neck and back.
- Four plantigrade legs, thick scale armour, large dark claws; a long
  powerful scaled tail.

## 2026-10-16 — NEW UNIT: Spike Beak 3 (artist reference sheet, the tall one)
Artist (verbatim): (pasted the tall SPIKE BEAK sheet) / "only comment on
spikebeak 3 is to make its face mask on the top portion above the eye extend
further back and its white feathers on stomach fo further back, and make its
tail longer as well".
Sheet: design/reference/spike_beak_3-character-sheet.png (the third SPIKE
BEAK sheet: small round = spike_beak, rounder mature white-chested =
spike_beak_2 (file pending), tall long-winged = spike_beak_3). Intake
--side-facing left --floor back=762 (the back view's tail tip hung to 822).
Same batch rules as the x14 entry (light flow, regular tier, placeholder
clips).

SPIKE BEAK 3 ("spike_beak_3", ~6.6 u).
- Tall vertically elongated blue bird (palette 210,218,236 / 127,165,227 /
  71,123,224 / 30,76,170 / 31,54,118, beak 224,173,91), clear white chest
  pattern, large long wings hanging below the legs, layered feathers.
- Head: an extended, wider integrated yellow BEAK MASK, a long sharp beak,
  a pronounced forehead spike, large expressive eyes in cream, layered head
  feathers.
- Strong sturdy yellow legs, three front toes, sharp curved dark claws.
- Tail: white-tipped layered tail feathers hanging behind.
Binding deviations from the sheet (artist):
- The mask's TOP portion, above the eye, extends further BACK over the head.
- The white stomach feathers wrap further BACK along the sides.
- The TAIL is longer.

## 2026-10-16 — Spike Beak v1 note (artist verbatim + 1 image)
Artist (on a crop of the side view, a red outline around the front wing flap
just behind the eye): "two wings  this one needs to be removed".
Binding reading: the side shows TWO wing shapes per side; the FRONT one (the
vertical feather flap behind the eye / cheek, in front of the main tucked
wing) is removed on both sides; the main tucked wing stays. The sheet draws
one small tucked wing per side.

## 2026-10-17 — Batch x15 v1 verdict (artist verbatim + 1 image)
Artist: "our characters should be symmetrical head turns are done through
posing so our models remain flexible - spring pig and the child frost giant
seem to have issues with this,  for example ember rex tail is moved to one
side, can we rig it so we can position it in the center or the side, just
want to make sure these things are considered, if its just posed that way
then thats fine but if not then there may be issues" / "can we make storm
bear fatter, bigger belly" / "ffor spring pig the end of tail should be wider
like the ref pick so it can believed like he's flying/floating with the tail"
(image: a blue bamboo-copter toy, a two-blade propeller on a stick; saved as
design/reference/spring_pig-v1-tail-propeller-reference.png) / "raven shade
also has symmetry issue and should have 2 eyes - can we make its last wing
feather red so it looks like a blood soaked blade" / "the image refs you gave
me I cant zoom in so hard to critique details" / "the head looks off for
drakescale" / "spikebeak 3 has readability issues and is hard to tell" / "we
need better images or a way so i can zoom and see details".
Binding reading:
- ALL units: the REST POSE is symmetric (head straight, legs paired, tail on
  the midline); head turns / swung tails are POSES in the rig. This resolves
  the light-flow tension: asymmetry from the proxy is removed by REPOSE
  (bones: proxy_finish_repose), not by mirror-averaging geometry; the face is
  carried rigidly, never re-sculpted. Named: spring_pig, frost_giant_child,
  ember_rex (tail: a tail chain that rests centred and can be posed to the
  side), raven_shade. Applies to every unit with a turned head / swung tail /
  staggered legs.
- Storm Bear: fatter, bigger belly.
- Spring Pig: the END of the spring tail widens into a two-blade PROPELLER like
  the bamboo-copter reference, so it reads as flying / floating on the tail.
- Raven Shade: two eyes (mirror the existing one), symmetric; its LAST wing
  feather on each wing (the longest, lowest tip) red, like a blood-soaked
  blade (sheet red 141,55,65 toward the tip).
- Drakescale Beast: the head looks off (detail pending the zoomable review).
- Spike Beak 3: readability issues (detail pending the zoomable review).
- Review: a zoomable way to inspect models (3D viewer + full-res renders).

## 2026-10-17 — NEW UNIT: Drakescale Cub (artist reference sheet)
Artist (verbatim): "here is another one to intake".
Sheet: design/reference/drakescale_cub-character-sheet.png (clipboard capture,
pixels unchanged). Intake --side-facing left --floor back=728,side=748 (the
auto floor took the tail tips). Same rules as the batch: Pixal3D light flow,
regular tier, placeholder clips, SYMMETRIC REST POSE by repose (2026-10-17
verdict). Front-vs-side height -9.9 % (the front view's paws are drawn lower,
in perspective).

DRAKESCALE CUB ("drakescale_cub", ~5.9 u): the young Drakescale Beast.
- Quadruped pangolin-dragon cub: layered brown pangolin scales (41,35,34 /
  71,54,47 / 126,84,63) with copper tips (191,145,112), cream belly / throat
  (224,189,157); grey accent 105,97,99.
- Head: smaller, youthful, pangolin-inspired snout, horn-like scale
  protrusions (smaller), softer orange eye, friendly curious expression.
- Smaller dorsal spines; lighter, slimmer silhouette; slimmer limbs; smaller
  rounded claws. Long tapered scaled tail.

## 2026-10-17 — Spike Beak 3 v1 notes (artist verbatim + 3 annotated images)
Artist: "the spikes should extend past and we can't see the eyes well" /
"lets make the white tufts extend further" / "and add larger tail, the second
part, in white".
Images: design/reference/spike_beak_3-v1-mask-spikes-annotation.png (side
head: two red lines carrying the mask's brow spikes from above the eye back
and up PAST the head feathers), spike_beak_3-v1-white-tufts-annotation.png
(front: two red lines from the chest down and outward past the feet),
spike_beak_3-v1-tail-annotation.png (back: a larger red outline hanging below
the current tail tip).
Binding reading:
- The mask's brow SPIKES (the v1 extension band) become long, distinct blade
  spikes that sweep back and up past the head's crest feathers (side view),
  one per side; yellow beak/mask colour.
- The EYES must read clearly: larger / higher-contrast eye (cream ring, dark
  iris) not hidden by the mask edge or cheek flap.
- The WHITE chest tufts extend further: longer white feather tips running down
  and outward in a V past the legs toward the floor (front view).
- A SECOND, larger TAIL section in WHITE below / behind the current tail tip
  (back view), so the tail reads longer and two-part.

## 2026-10-17 — Ember Rex 2 v1 notes (artist verbatim + 2 images)
Artist: "ember rex 2 cant see the eye" / "the flames here  dont contract well
enough with the body - that goes for all the actual fire parts like its
feathers and crest on its head and the chest flame".
Images: design/reference/ember_rex_2-v1-eye-annotation.png (head close-up,
no readable eye), ember_rex_2-v1-flame-collar-annotation.png (the flame
collar band across the neck / shoulders outlined in blue).
Binding reading ("contract" = contrast):
- The EYE must read: a clear eye on each side of the head (the sheet's
  intense, regal yellow-orange eye with a dark pupil), crisp, not lost in the
  red.
- Every FIRE part - the flame collar (outlined), the flame crest, the
  flame-tipped feathers / wing and tail flames, the chest flame - must
  CONTRAST strongly with the body: brighter, more saturated orange -> yellow
  (sheet 253,124,67 / 253,201,100, hot cores toward pale yellow) and
  stronger glow, against a darker, more muted red body (sheet 68,38,42 /
  161,42,48). The body itself is not lit.

## 2026-10-17 — Drakescale Beast v1 head note (artist verbatim + 1 image)
Artist: "compare this head to the ref, its way too compressed and looks
smooshed where we cant see eyes or any other face details".
Image: design/reference/drakescale_beast-v1-head-annotation.png (the v1 head
in side view: a short, crushed muzzle; no readable eye).
Binding reading: the head must match the sheet's HEAD DETAIL panel: a longer
pangolin-dragon SNOUT (not compressed), a clear narrow ORANGE EYE on each
side, readable face detail (the cream lower jaw / mouth line, nostril, brow
ridge), and the horn-like scale protrusions swept back above it.

## 2026-10-17 — NEW UNIT: Spike Beak 2 (artist reference sheet)
Artist (verbatim): "spike beak 2 is essentially a smaller simpler version of 3".
Sheet: design/reference/spike_beak_2-character-sheet.png (webp -> png, pixels
unchanged; the mid Spike Beak, after the small round spike_beak and before
the tall spike_beak_3). Smart intake, --side-facing left. Same rules as the
batch: Pixal3D light flow, regular tier, placeholder clips, SYMMETRIC REST
POSE by repose.

SPIKE BEAK 2 ("spike_beak_2", ~5.9 u).
- A large, round, powerful blue bird (205,210,222 / 130,158,199 / 72,117,188 /
  47,82,148 / 39,57,103), rounded tucked wings, layered feathers, white
  accent feathers in a V on the chest and a white collar band.
- Head: an integrated yellow beak MASK (215,170,94), a long sharp beak, the
  forehead spike continuing the mask, a LARGE expressive eye (cream ring, dark
  navy iris), layered head feathers; mature, intimidating look.
- Strong sturdy yellow legs, three front toes, sharp curved dark claws.
- Tail: a short layered tail with white tips (back view).
Reading of "smaller simpler version of 3": the same design language as
Spike Beak 3 (mask, spike, white V, blue layers) on a rounder, simpler body.
The artist's Spike Beak 3 notes (readable eyes, a clear mask spike) apply in
spirit: the eye must read clearly and the mask / spike must read as one
yellow shape.

## 2026-10-17 — Spring Pig v2 face note (artist verbatim + 1 image)
Artist: "there are massive symmetry issues with spring big on the face  it
was done to match the picture only but here one eye sticks out there is more
correct but looks broken and the snout is on the side" / "ears also looked
crooked". Image: design/reference/spring_pig-v2-face-annotation.png.
Binding reading: the face is rebuilt symmetric: snout centred pointing
forward with two nostrils on its front face, two goggle lenses at equal
height on one strap, ears matched. Done by RE-GENERATING the proxy without
the sheet's 3/4 front (design/reference/spring_pig/proxy/ notes); the
propeller tail (v2) carries over.

## 2026-10-17 — Raven Shade v2 face note (artist verbatim + 1 image)
Artist: "the face looks crooked" (image, pasted inline: the v2 head from the
front: the beak bends to one side, the eyes sit at different heights).
Binding reading: the head must be truly symmetric - beak straight on the
midline, both eyes level and matched. The v2 repose only turned the head
rigidly; the asymmetry is SCULPTED into the proxy head (the sheet's front and
back are 3/4). Fix: a HEAD GRAFT like Drakescale Beast's - a head-only
Pixal3D mesh from the sheet's FACE / HEAD DETAIL panel, its drawn half
mirrored, placed on the body (design/reference/raven_shade/head_graft/).

## 2026-10-17 — Spring Pig v3 snout note (artist verbatim)
Artist (on v3): "ah yes it updated now, the snout needs to be cynlidrincal".
Binding reading: replace v3's wide flat box snout with a CYLINDER pointing
straight forward (-y) on the midline: a round cross-section, a flat front
disc carrying the two nostrils, sized from the sheet's side view (length and
height of the drawn snout) - near-square in profile, round from the front.
Everything else in v3 stays (symmetric head, level lenses, ears, propeller).

## 2026-10-17 — Drakescale Beast v2 tail note (artist verbatim + 1 image)
Artist: "why is the tail  different here, lets just extend and duplicate
exisiting tails and end in a point". Image:
design/reference/drakescale_beast-v2-tail-annotation.png (the smooth banded
tube tail next to the scaled body).
Binding reading: drop the v1/v2 capsule TUBE tail. Build the tail from the
model's OWN scaled tail: take the generated tail-root segment (pangolin scale
plates + its texture) and DUPLICATE it along the sheet's side-view tail curve,
each copy slightly smaller, tapering to a POINT at the tip. Same scales and
colours as the body; centred on the tail chain (posable). Applies to any unit
where a tail had to be lengthened (house rule from now on: extend tails by
duplicating the generated tail segments, never a smooth tube).

## 2026-10-17 — Spring Pig v3.1 face note (artist verbatim + 1 image)
Artist: "smooth out the face and move the eyes a bit closer to center".
Image: design/reference/spring_pig-v31-face-annotation.png (blue marks on the
seams of the rebuilt cheek patch: the patch's top edge under the strap and
its jagged left/right edges, plus the dark cheek blotches).
Binding reading: (1) the face reads as ONE smooth surface: no patch outline,
no ledge or seam where the v3.1 cheek rebuild meets the head, no dark
blotches (the normal-map fix previewed earlier) - geometry smoothed across
the seam and the albedo / normals blended; (2) the two goggle lenses (and
their brass rings + the strap ends at them) move a bit closer to the centre,
symmetric about x = 0, staying level on the strap. Snout cylinder, nostrils,
ears, propeller unchanged.

## 2026-10-17 — Batch pause + carry-over notes (artist verbatim)
Artist: "after this pause work, a lot of models have issues with the eyes, or
tails goin under the body when it doesnt make sense to but can pick another
time".
Binding reading (next session, across the batch):
- EYES: many units' eyes read poorly (too small / soft / dark / hidden by the
  mask or cheek, or painted low-res). Review every unit's eyes in the viewer
  and fix them so they read clearly at game distance (crisp shape, contrast,
  the sheet's colour; geometry where the socket is lost).
- TAILS: tails curling forward UNDER the body where the sheet does not mean it
  (e.g. Drakescale Beast's J-curve under the belly). Rest tails trail BEHIND
  the body, centred, unless the sheet clearly draws otherwise; the tail chain
  still allows posing.

## 2026-10-24 — NEW UNIT: Duskmaw re-run, two forms (artist reference sheets x2: Shadow Abyss + Shadow Abyss True Form)

References: design/reference/duskmaw-character-sheet.webp (SHADOW ABYSS, base
form) and design/reference/duskmaw_true_form-character-sheet.webp (SHADOW ABYSS
- TRUE FORM). Intakes: design/reference/duskmaw/ and design/reference/duskmaw_true_form/.

Artist (verbatim): "run duskmaw again with our new improvements and new
references

so base form is the first image and it shifts into the second image, these
maybe should two separate models to keep things easier than a full morph or
not sure follow best practices and our previous intakes"

Decision (orchestrator, best practice + the 2026-09-25 Vineweave precedent):
TWO SEPARATE MODELS, swapped in game under a transition, not a morph.
- `duskmaw` = the base form (REPLACES the procedural v4; v4 stays in git at
  6b9e2e1). `duskmaw_true_form` = a new unit.
- Why not a morph: glTF / Godot morph targets need one mesh with identical
  vertices in both shapes; two Pixal3D meshes never share topology, and the
  true form is ~2.5x the size with a hollow maw. A swap is also what the
  game needs anyway (separate collision / size / skills per form).
- The shift = each model carries a transition clip (base `transform_out`:
  sinks / dissolves down into its tendrils; true form `transform_in`: rises
  out of its tendrils), same origin, same facing (-Y), same palette chips;
  the game swaps the scene at the clip boundary under a shadow-burst VFX.
  Clips are PLACEHOLDER until the artist confirms the feel.

Binding reading — BASE FORM (`duskmaw`, sheet 1, front / back / side true orthos):
- Tall slender shadow humanoid, near-black matte "smoke" body.
- HEAD: sharp crown-like head (a tall central spike + swept points, a hat-brim
  ridge), facial area shadowed, two GLOWING yellow eyes (slanted), red-tipped
  spikes. Simple, elegant shapes.
- Shoulders: a red-tipped spike rising from each shoulder.
- TORSO: a central jagged mouth-like ABYSS - a vertical TEAR in the body,
  red rim, inner orange-to-yellow glow, zigzag edges; NO eyes or features in
  it (it is not a toothed mouth: the v2-v4 body maw with teeth is superseded).
- ARMS: long, tapered, hanging; sharp claw fingers with RED tips; ragged
  shadow extensions on the forearms.
- BASE: the body flares into many shadow TENDRILS flowing out along the
  ground, curling (liquid silhouette).
- Palette chips: 37,36,41 / 54,56,68 / 62,58,69 (shadow tones), 170,52,57
  (red), 254,136,75 (orange), 253,223,149 (pale yellow glow).
- GLOW (sheet draws it): the eyes and the torso tear's inner glow.

Binding reading — TRUE FORM (`duskmaw_true_form`, sheet 2):
- The base form's head becomes a DECOY HEAD ("fake head to lure prey, not the
  true body"): the same crown shape + glowing yellow eyes, sitting on top.
- TRUE BODY = a MASSIVE HOLLOW MAW filling the whole front: red flesh lips,
  multiple layers of cream fangs (upper + lower), deep dark-red ridged
  interior (abyssal), organic flesh-like.
- Large reaching tendril ARMS emerging from the body, whip-like, red claw
  ends; red-tipped spikes on the upper body.
- BASE: many tendrils blending into the ground (liquid smoke form).
- MOUTH STATES drawn: OPEN (luring) = the turnaround; CHOMP (attack) = jaws
  half closed, teeth interlocking; CLOSED (rest) = jaws shut, fangs crossed.
  Rest pose = OPEN (as the three views draw it); the rig gets jaw bones so
  chomp / closed are clips.
- SCALE COMPARISON panel: ~2.5x a human figure (human 87 px vs creature
  ~220 px tall).
- Palette chips: 42,41,46 / 59,59,63 / 69,71,84, 122,43,49 (dark red),
  201,60,62 (red), 253,124,77 (orange), 252,234,167 (cream teeth / eye glow).
- GLOW: the decoy head's eyes. The maw is NOT drawn emissive.

Both: Pixal3D light flow (proxy as the base mesh, light finish, symmetric
rest pose by repose only where it does not touch the face). Regular tier.
Known answers carried from the old duskmaw: movement = GLIDE (2026-09-25),
in game 1.80 m. The old "idle chomp" applied to the toothed body maw; the new
base form's torso tear has no teeth, so the base idle becomes a glow pulse +
tendril sway (PLACEHOLDER), and chomp moves to the true form.
PLACEHOLDER: every clip (idle, glide, transform_out / transform_in, true-form
chomp / close) until the artist answers OPEN-QUESTIONS "Duskmaw (two forms)".
Intake flags: base `--side-facing right --ortho-views front,back,side --axis
front=246` (the front is a true front; the auto 3/4 flag came from the
asymmetric arms / tendrils); true form `--ortho-views front,back,side --axis
front=255` (axis on the decoy head + maw centre).

## 2026-10-24 — NEW UNIT: Vampire Warrior re-run (artist reference sheet, re-supplied)

Reference: design/reference/vampwarrior-character-sheet.png (the 2026-09-26
sheet, re-pasted by the artist and saved this time; the original was never
on disk). Intake: design/reference/vampwarrior/.

Artist (verbatim): "rerun vampire warrior again as well with our improvements
we should still have the reference"

Binding reading: rebuild `vampwarrior` on the Pixal3D light flow, replacing
the procedural v4.2 (kept in git at 0a6da6f / 6fd5808). The sheet
transcription of 2026-09-26 stands (tall female vampire warrior, pale skin,
red eyes, fangs, waist-long bone-white hair parted centre, black high-collar
armoured bodice with silver trim + red throat gem, long torn black cape with
dark-red lining, long black gloves, black thigh-high heeled boots with
armoured knees, bare upper thighs; a leaf / flame-shaped bone-pale sword
nearly her height held point-down in her right hand, simple handle, red
tassel). Palette chips sampled: 177,164,156 / 122,113,108 / 53,49,49 /
79,49,52 / 44,33,35 / 33,28,28.
Artist notes from v2-v4.2 that are about the CHARACTER (not the old
procedural build) and still bind: warm pale grey-mauve skin (not Blender
grey; design/reference/vampwarrior-v4-face-reference.png), thick brows,
darker less-bright red eyes, fangs emerging from UNDER the upper lip (no
fang root visible at the lip edge), ONE large front strand per side framing
the face with the rest of the hair hugging the scalp, a faint (not heavy)
lip red, no outline shells / cel banding, hair + cape follow-through in the
clips (no biped stiffness).
Sword: a separate rigid mesh on its own bone (the old build had
vampwarrior_sword too), from the SWORD DETAIL view if the proxy fuses it.
Known answers carried: in game 1.88 m; clips idle + walk (biped).
PLACEHOLDER: clips until movement is confirmed.
Intake flags: --region 0,90,1055,935 --cuts 442,790 --side-facing left
--axis front=233 --ortho-views front,back,side, plus the cape carve fix
(intake_notes: union masks with a --bg-tol 6 pass, proxy-prep sheet with
R+5 on in-silhouette near-background pixels).

## 2026-10-24 — Duskmaw v5 head / crown note (artist verbatim)
"yea the renders look off on the head and crown, seems it couldnt tell the
shadows from the face and the assymetry on crown these things should be fixed"

Binding reading: (1) the face: Pixal3D sculpted the sheet's face SHADING as
surface features (brow / nose ridges, the orange V band and pink mouth the
v5 paint covered) - the face must read as the sheet's smooth shadowed face
under the brim with the two slanted yellow eyes. (2) the crown must be
SYMMETRIC (the v5 brim is crooked and the head sits ~0.1 u off the body
axis). Fix at the source (proxy-three-quarter-sheets lesson): regenerate
the proxy from symmetric inputs - the front / back head rows mirrored about
the sheet axis (the half carrying the side spike, so both sides get it),
side as `right` + side mirrored as `left`; keep the light finish. The
artist's ask overrides "never symmetrise the head" for this unit's crown.

## 2026-10-24 — Vampire Warrior v5 verdict (artist verbatim)
"vampire warrior seems like its too low quality, compared to our first vampire
warrior this seems better clothes and shape but like a low render of what it
should be"

Binding reading: KEEP the v5 direction (clothes + shape beat v4.2); RAISE the
fidelity. Causes measured: 18.5k tris (regular tier) from a 198k-face proxy,
a 2048 albedo, the proxy's own 2048 texture, and a face only ~60 px tall in
the sheet views. v5.1: HERO tier per docs/lane-conventions.md "Quality tier
is a per-asset knob" (30-50k tris, ~40k), 4096 bakes, a proxy regenerated
at texture_resolution 4096 if the card allows, the HEAD grafted from a
head-only Pixal3D of the sheet's face panel (light.head_graft, drakescale
precedent), and the sword rebuilt from the sheet's SWORD DETAIL silhouette
(the sandbox's saw-step blade did not match).

## 2026-10-24 — Duskmaw base form: mouth-open idle (artist verbatim + 1 sheet)
Reference: design/reference/duskmaw-mouth-open-sheet.webp (SHADOW ABYSS, base
form with the TORSO MOUTH open: "true mouth of the creature; wide, deep,
hollow maw; strong upper and lower jaw; jagged predatory teeth; abyssal
interior; appears like a huge mouth in the torso; main feeding organ; no
eyes or features here"; the head is now a LURE head: "small, deceptive head,
acts as a lure to attract prey, facial area is fake, glowing eyes, not the
true mouth"). Intake: design/reference/duskmaw_open/.

Artist (verbatim): "also for duskmaw, it should switch between whats built
and this with the mouth more open for idle animations, the full mouth is a
full transformation but this one should alternate with the base form"

Binding reading: the base `duskmaw` gets a TORSO MOUTH that opens and closes.
CLOSED = the built base form (the glowing jagged tear of sheet 1 is the
mouth shut: the interlocking glowing teeth); OPEN = this sheet (wide maw,
red / orange / yellow glowing jagged teeth, dark abyssal interior). The idle
ALTERNATES between the two. ONE model: built from this mouth-open sheet's
proxy (it has the cavity and the teeth; the closed sheet's body has none),
with upper / lower torso-jaw bones that close it to the tear. The TRUE FORM
(sheet 2) stays a separate model = the full transformation.

## 2026-10-25 — Vampire Warrior v5.1 face + feet (artist verbatim + 2 images)
Artist (on a v5.1 face close-up): "why is it look unpolished and choppy".
Orchestrator diagnosis (measured): UV layout 6605 islands covering 27 % of the
4096 map at a 0.001 margin (bleed cracks on every island edge, low face texel
density); every face flat-shaded (contract) so each triangle is a facet; the
albedo is the proxy's noisy projected texture + the graft's (tones differ).
Artist: "do all fixes, the other issue is her feet thats not a realistic
foot, can we just make a flat foot like our regular old model" (image: the
v5.1 foot, a pointed heeled stump).
Binding reading (v5.2): (1) re-unwrap - few large islands (face and hair
front each one island), ~65 % packing, a real margin + dilation; (2) SMOOTH
shading on the face + hair as declared smooth regions (the contract's
hair-family exemption) - an explicit artist choice for this unit, the rest
stays flat; (3) the face skin repainted as clean flat tones with the sheet
face panel's drawn features (brows, red eyes, lips, fangs from under the lip);
(4) FEET: replace the proxy's pointed heeled feet with FLAT feet like the
v4.2 procedural model's (flat-soled boot feet), closed parts on the foot
bones, boot black, planted on the floor.

## 2026-10-25 — Duskmaw v5.2 verdict (artist verbatim + 3 images)
Images: the v5.2 head close-up; closed sheet | v5.2 closed; open sheet | v5.2 open.
Artist: "duskmaw face seems to have suffered here as well and the closed does
not match
the open one - the inside of ours is black but in the ref its red"
Binding reading (v5.3): (1) FACE: the same polish fixes as vampwarrior v5.2 -
re-unwrap (few large islands, real margin), a clean face paint (one smooth
shadow tone under the brim, crisp slanted yellow eyes, no blotches), smooth
shading on the face only; crisp red spike TIPS (clean boundary, not blotches).
(2) CLOSED must match the closed sheet: a NARROW vertical jagged TEAR (about
0.6 u wide at the chest: red rim, orange -> yellow zigzag core), the slim
torso around it - not the two wide teeth rows pressed together. (3) OPEN
interior is RED like the sheet (deep dark red at the back, lighter red toward
the teeth), not black.

## 2026-10-25 — Vampire Warrior v5.2 face + Duskmaw v5.3.2 closed (artist verbatim)
"the front render for the face looks good, has dark spots that should be fixed
but the side is now off - coordinate with a codex agent to generate references
as needed

also duskmaw mouth closed, it looks decimated instead of the sleek clean look"
+ "can also coordinate with a codex agent for a visual review as well"

Binding reading: (1) VAMPWARRIOR face: keep the v5.2 front read; remove the
dark spots; fix the SIDE (the graft is a single front-view generation, so its
sides are a flat block): Codex generates head references in the sheet style
(left / right profile, back, full front) -> a multi-view head generation ->
re-graft. (2) DUSKMAW closed: a SLEEK clean torso with a crisp tear like the
closed sheet, not crushed teeth / folded walls. (3) A Codex visual review of
all three units against their sheets feeds the next lane briefs.
Codex visual review (2026-10-25, of the v5.3.2 / v1.1 / v5.2 renders vs the
sheets): design/codex-review-2026-10-25-duskmaw-vampwarrior.md. Orchestrator
filter: NOT taken where it contradicts the sheet (the sword DOES have a
mid-blade binding band and the waist ONE ring on the sheet) or the light flow
(full retopology); taken: duskmaw closed lips over tucked teeth, open-maw tooth
rows without crossing fragments, clean red tip masks, tapered claws;
vampwarrior low wedge flat feet, softer nose / chin in profile, face dark
islands, softer mouth surround. True-form items held for the artist.

## 2026-10-25 — Quality verdict on vampwarrior v5.3 + duskmaw v5.4 (artist verbatim)
"have another agent actually review these  its clumpy like poorly sculpted"
"same for duskmaw he just looks destroyed on parts"
"would a full re-run be better at this point, of the face"
"or certain parts so it matchs better"
Orchestrator: patching paused. An independent Codex review (raw proxy vs
finished) + a stage-by-stage diagnostic (raw -> watertight remesh ->
decimation -> bake -> patches) decide between a full re-run and a targeted
re-generation of parts. Duskmaw v5.4's full run is held (its no-shell patch is
applied; vulture_creature still 5b3aaa03830de189).

## 2026-10-25 — Re-run decision (artist verbatim)
"make the fices" (= make the fixes, after the diagnosis: the watertight remesh
grid fuses strands / muddies the maw; the raw head4 has a stitched zigzag mouth
and a flat profile).
Plan: (1) kit: opt-in finer remesh grid inside named boxes (head / face /
maw); (2) vampwarrior: Codex head references v2 (closed / softly parted mouth,
small fangs under the lip, + 3/4 views, clean grouped locks) -> a new
multi-view head -> the WHOLE head incl. hair replaces the proxy's (its turned
head cap removed) -> clean light finish on the fine grid, the stacked repaint
patches dropped; (3) duskmaw: Codex clean turnaround references (closed +
open, front / side / back) -> a new proxy -> the light finish on the fine
grid with a real maw triangle share (~22k body); the v5.4 rig / tear / clip
work carried over; (4) duskmaw_true_form: the fine-grid re-run.

## 2026-10-26 — Duskmaw v6.2 (artist verbatim)
Shown as a direction check (v5.4's real maw grafted into the clean v6 body; lid + tear when closed; DIGEST
12e32496b661a1c1). Artist: "duskmaw is fine for now". v6.2 stands; the v6.3 refinement (graft fit into the slim
torso, sharper red-based teeth, darker throat; Codex review scratch codex_dusk62) was stopped and is held as known
follow-ups, not shipped.

## 2026-10-26 — Duskmaw import into Conquest + Dungeon Diver (artist verbatim)
"import duskmaw into the two games dungeon diver and conquest, coordinate with agents from those projects and hand
off to them, the true form duskmaw is a form shift on ultimate"
Binding reading: base v6.2 (12e32496b661a1c1) + true form v1.1 (46e5e5268b5132c1) go into BOTH games, each import
handed to a session working in that game's repo. MECHANIC (answers the trigger part of D1): the true form is a FORM
SHIFT on Duskmaw's ULTIMATE: base `transform_out` -> model swap under VFX -> true form `transform_in` -> true form for
the ult, then back. Movement feel + true-form size still open (D1 / D2). Handoff: design/handoff-2026-10-26-duskmaw-import.md.
- Import results (2026-10-26, both unmerged, unpushed):
  - Conquest: claude/duskmaw-forge-import-6a7761 @ 6037c30. Base root_scale 0.04316 -> 0.25004 (1.80 m); true form 4.50 m
    (placeholder). The form shift sits on Voidstep's teleport half: 2 turns, purple burst swap, chomp in true form,
    close -> swap back. Look-only (stats unchanged). Tests 4224/4226 (0 failing).
  - Dungeon Diver: claude/duskmaw-forge-import-056353 @ dd2a8a8 + 49cd2bd. Duskfall (R): 10 s true form, transform
    clips at 1.5x, true form 5.75 m (placeholder); boss = new model only. Test 22/22. The MP owner must hear about
    rpc_hero_shift in world.gd.
  - Forge follow-ups: (a) the true form v1.1's teeth do not glow (only its eyes): intended? (b) cyclic clips repeat
    their first frame at the end (a one-frame loop hitch); (c) the true form's bind / rest pose is a tilted floating
    mass, not a standing pose.

## 2026-10-26 — Vampire Warrior: full regen (v7) + decisions for v7.1 (artist)
After the v6.1 Codex review (hair ropes, magenta back, muddied face, noisy v5 body), the artist chose a FULL REGEN
(option "Full regen"): a new body from a clean Codex full-body turnaround (design/reference/vampwarrior/codex_body_v6/,
flat boots, no sword) -> Pixal3D proxy_v6/vamp_body6_s11.glb (Codex pick) + the head6 graft + the traced sword.
v7 (DIGEST 328cdef8eb1d1e12) Codex review: body FIXED, back hair improved, face + head hair NOT fixed.
Artist decisions for v7.1 (multiple choice): HEAD = "Face-only graft" (keep the body's own hair, graft only
head6's face); SWORD = "Full sword, pose in clips" (rest pose symmetric arms-down; the idle / walk clips raise the
right arm to hold the full-length sword point-down like the sheet). Artist rule: tell them review issues before
any rebuild; lanes cap previews (~3).

## 2026-10-26 — Vampire Warrior: face first, then hair (artist verbatim)
Artist: "what are best practices for hair, seems we are trying to do both at one time, is that traditionally done
that way, seems we could have a good face and then good hair" / "i think that'd be best as the image has face
features over hair which is a style choice for 2d art but doesnt work on 3d".
Binding reading: build in the standard order. (1) FACE PASS: a bald head / smooth scalp with the grafted face, no
hair; Codex review, then artist approval; nothing hair-related. (2) HAIR PASS afterwards: hair as separate pieces on
top of the approved head (traced lock strips like the sword, own sway bones), never touching the face. Rule: sheet
features drawn OVER hair (brows / eye lines across locks) are a 2D style convention; in 3D the face lives on the
skin under the hair.

## 2026-10-26 — Vampire Warrior head7 references (artist verbatim)
"we want it to resemble our reference but at the quality of studios" / "we can try a better reference image, and do
we still think head first is better" / "go ahead with pointed ears".
head7_B_render: Codex drew OUR sheet's face as a stylized 3D game render (no line art; real eyeballs, crisp colour
patches), as a bald head for generation plus a with-hair target (separate clean locks clear of the face) for the
later hair pass. Face first stays: our watertight remesh fuses any close locks, so hair must be separate geometry
anyway. POINTED EARS: introduced by the orchestrator's prompt (the sheet panel shows a human ear); the artist chose to
keep them (a deliberate deviation from the sheet).

## 2026-10-27 — Pipeline rules approved + head route (artist, multiple choice)
"Approve rules" (heads off the voxel remesh as their own parts with per-part bake + smooth shading; body stays on
the remesh for now; smooth-shaded masters allowed with a declared shading mode; budgets as guidance; measured
checks) and head route "Animatable hybrid" (the head7 look baked onto a corrected template topology with eyelid / mouth
loops, eyeballs, mouth interior, fangs, jaw weights, blink / talk shapes). Earlier, re: the kit estimate: "i dont
believe your 2-4 day estimate why so long ... if it will improve our work why havent we started it"; the
finish-independent kit items started in parallel (session kit-head-path). The kit speed-up branch was merged on
the artist's "if its good why havent we merged it" (b151e0f).

## 2026-10-27 — Vampire Warrior: quality bar, route, animation scope (artist verbatim)
Session 6cf4305c. Handoff: design/handoff-2026-10-27b-quality-bar-and-route.md. Codex session review:
design/review-2026-10-27-codex-session-review.md.
- On the H2 head build: "this is clearly not acceptable and there was issues on the body now, did the previous agent
  not document properly or did you not read the handoff doc" / "didnt the handoff doc have improvements to the
  pipeline so this doesnt happen".
- "would a full regen of head and body be better at this point, with the bald references then the hair later ... seems
  we left the previous chin shouldnt we just remove the entire head if we're doing transplants".
- "to me it seems we are still trying to graft the face on to the previous body with issues, and the cape on the body
  stretches to the sword, I thought we had said we werent going to do that anymore" -> option 1: the cape as its own
  part (approved).
- "our work seems to be getting worse or only improving one area, while regressing in the rest" -> a Codex review of
  the session (requested).
- Goal: "we want models at the level of league of leggends or marvel rivals"; "for now the gameready ones are fine,
  but Id like to do in game animations we these and mini animated shorts as well".
- "dont need full facial rig for gameplay - if thats the hard part can leave it out for now"; "animation is more
  movement animations like swinging a sword"; "yes animation like attack moves or spells in the ggames".
- On estimates: "i also dont believe your 1-2 day estimate"; on a proposed timeboxed spike: "no thats a method to
  success we want models at the level of league of leggends or marvel rivals".
Open (ask): the bar confirmation, the route for the art-heavy parts (A artist / B researched tools / C both), the
motion source, the look of the shorts, the first hero.
