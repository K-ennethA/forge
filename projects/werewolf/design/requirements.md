# Werewolf player character — requirements (finalized)

Reference read: `design/refs/image-1789661674658.png` (3-variant turnaround, front 3/4)
and `design/refs/image-1789661719013.png` (head/torso close-up). Both are full-werewolf-form
only — no human-form or teen-wolf-form reference exists yet (see §7, below — this is the
open item before the human base can be generated).

## 1. Three-form architecture

- **Form A — Human.** Athletic adult male, base mesh, base skeleton (the "biped" rig).
- **Form B — Teen-wolf.** SAME mesh as Form A, SAME skeleton, SAME collision capsule and
  SAME bone lengths — no mechanical difference from Form A at all. Visual-only delta via:
  - Shape keys (blend shapes) on the head: brow ridge, pointed ears, sideburn volume, fang
    protrusion.
  - A material/shader swap: skin shader gains a subtle fur-hint normal map on the forearms/
    calves, eyes switch to an emissive gold shader.
  - No new bones, no new topology, no scale change. This form exists so combat animations
    authored once on Form A play back identically on Form B.
- **Form C — Full werewolf.** A SEPARATE, larger mesh on a SEPARATE (but superset) skeleton.
  Van Helsing-film style: digitigrade hind-leg-like legs, elongated forearms, forward-leaning
  torso, wolf head, full properly-proportioned tail (not a stub). Not a shape-key delta of
  Form A — the topology and proportions diverge too far (digitigrade foot, longer spine
  curve, tail) for blend shapes to carry it.

## 2. Skeleton — superset, not a fork

Form C's skeleton contains every bone Forms A/B use, with IDENTICAL names, under the SAME
parent hierarchy, plus additions:

- Shared chain (same names, same hierarchy, different lengths/scale only):
  pelvis → spine_01..03 → neck → head; clavicle → upperarm → lowerarm → hand → fingers;
  thigh → shin → foot → toe.
- Form C adds (does not rename or remove anything above):
  - `tail_01..06` off the pelvis — a 6-bone chain (up from an earlier 4-bone stub plan) to
    carry a full, properly-proportioned tail with natural sway rather than a short stub.
  - `foot_metatarsal` inserted between `shin` and `foot` on each leg — this is what gives
    the digitigrade silhouette; Forms A/B do not have it.
  - One extra `spine_04` for the werewolf's forward hunch — Forms A/B use 3 spine bones,
    Form C uses 4.

- **Upper-body / combat retargets reasonably.** Punches, grabs, hit-reacts, blocks driven by
  the shared clavicle→hand chain carry over from Form A/B to Form C because the bone count
  and rotation logic match; only the bone lengths differ, which Godot's retarget handles as
  scale.
- **Locomotion does NOT retarget.** A human walk/run cycle played on Form C's legs will
  slide and float — the `foot_metatarsal` bone has nothing to copy from on Forms A/B, and
  toe-walking weight distribution differs fundamentally from a heel-strike human gait.
  Budget for dedicated werewolf locomotion animations (idle, walk, run, combat-stance
  shuffle) authored directly on Form C.

## 3. Proportions and height per form (confirmed numbers)

**Form A — Human — 188 cm (6'2") confirmed**
- Head + neck: ~29 cm · Torso (shoulder to hip): ~63 cm · Thigh: ~47 cm · Shin: ~49 cm
- Shoulder width: ~50 cm
- Arm length (shoulder to wrist): ~64 cm
- Combat reach radius (torso center to fist, arm extended): ~1.0 m

**Form B — Teen-wolf**
- Identical to Form A in every measurement above — zero deltas to height, reach, or leg
  length. Local deltas only: brow ridge +8 mm forward, canine (fang) length +12 mm, ear tip
  extended ~25 mm and rotated to a point, sideburn volume added along the jaw, eyes swap to
  an emissive gold material.

**Form C — Full werewolf — 244 cm (8'0") confirmed, ≈1.30x Form A**
- Head + neck: ~38 cm · Torso: ~81 cm · Thigh: ~61 cm · Shin (to hock): ~63 cm
- Of the 244 cm total, roughly **18 cm comes from the `foot_metatarsal` segment itself** —
  i.e. scaling Form A's leg proportions straight up would land closer to ~226 cm; the
  elevated digitigrade hock is what makes up the rest of the height, which is why the
  silhouette reads as much taller than a simple 1.3x scale would suggest.
- Shoulder width: ~78 cm (broader than a straight 1.3x scale of Form A's 50 cm — werewolf
  is bulkier, not just taller; this extra broadening is an ASSUMED styling choice)
- Arm length (shoulder to wrist): ~95 cm (also broader than straight 1.3x scaling — the
  "long arm" read in your references comes from this extra elongation plus the forward lean)
- Combat reach radius: ~1.5 m, vs Form A's ~1.0 m — this is the number that drives melee
  hitbox/attack-range design, not the height figure
- Tail: full, properly proportioned, **not a stub** — ~85 cm, roughly a third of standing
  height, reaching to about knee height when hanging relaxed. Carried on the 6-bone
  `tail_01..06` chain from §2. (Exact length is my proportion estimate from a generic
  "large canine, bipedal" ratio — no reference shows a tail clearly, so treat 85 cm as a
  placeholder you can correct once a tail reference exists, not a locked number.)

## 4. The transformation moment — mid-combat, fast, state-preserving

This is the part of the design that changed most from the first draft: it does **not** run
as a cutscene. It has to trigger while the player is already fighting and hand control back
fast enough that it reads as a combat move, not a pause in one.

**Timing — target ≈0.8 s total, locked:**
1. **Wind-up (~0.35 s)** — a compact upper-body hunch/flinch, not a full-body cinematic
   beat. Short enough to layer on top of whatever the character was just doing (idle,
   recovering from a swing, blocking) rather than requiring a clean idle state to trigger
   from.
2. **VFX-masked swap (~0.15 s)** — a single obscuring beat (flash / fur-burst particle /
   brief silhouette blackout) exactly at the frame the mesh+rig actually swaps.
3. **Recover (~0.3 s)** — a short burst into Form C's combat-ready stance. Input is
   accepted again at the end of this beat, already facing the same direction the player
   was facing when the transform started.

**State that must carry through the swap — an implementation note, not a modeling one:**
- **Facing & position.** Form C's root bone is placed at Form B's root bone's exact
  position and yaw at the swap frame — no snap, no re-center.
- **Momentum.** Velocity lives on the character controller, not the skeleton. As long as
  the transform swaps the mesh/skeleton *child* under the same controller node rather than
  destroying and re-instantiating the character, momentum is never touched by the swap —
  this is the one implementation detail worth getting right early, because getting it
  wrong (rebuilding the character node on transform) is the natural-looking mistake that
  would silently kill momentum, facing, and target-lock all at once.
- **Current target / lock-on / aggro.** Same reasoning — that state lives in the combat/AI
  layer keyed to the controller's identity, not to which mesh is currently attached to it,
  so it survives automatically under the same condition above.

**Combat-balance flags (ASSUMED — yours to tune, not a modeling decision):**
- Treat the ~0.35 s wind-up like a committed attack windup (brief hyper-armor / partial
  damage reduction) rather than full invulnerability, consistent with how a "super move"
  startup is usually protected in Arkham-style combat. Full invulnerability would make
  transforming a free defensive panic-button; zero protection would make it worse than just
  attacking normally.
- Once wind-up begins, the transform is not cancellable (avoids ambiguous half-states);
  the recover beat is short specifically so the "locked out of input" window stays small
  rather than needing to make it interruptible mid-swap.

**Reverting (werewolf → human)** needs the same three-part treatment in reverse, as its own
animation set — collapsing down into a smaller frame reads differently in time than rising
into a larger one, so it isn't just the forward sequence played backward.

## 5. Export target

Godot, via `rigforge_export_godot`. Shared bone names between Form A/B's skeleton and Form
C's skeleton are what make the retarget work on the Godot side.

## 6. Reference images — status

- **Form C (full werewolf) — mostly covered.** Have: front 3/4 turnaround (3 color
  variants) and a head/torso close-up. Still want, not blocking: one clean BACK view
  (settles the tail's exact proportions and the spine hunch profile) and one view that
  actually shows the digitigrade foot resting on the ground.
- **Form B (teen-wolf) — not blocking.** Built as shape-key deltas on Form A's head from the
  written description (ridged brow, sideburns, pointed ears, fangs, gold glowing eyes); a
  face-focused reference would sharpen it but isn't required to start.
- **Form A (human) — the open item.** See §7 below — this is what's needed before the human
  base can be generated.

## 7. What's needed before generating the human base — RESOLVED, design approved

Closed out on 2026-09-17. Reference added: `design/refs/Uncharted-4-Nathan-Drake-character-
model.jpg` (a 4-pose contact sheet, head-to-mid-thigh, no full-body or back view in it).

- **Reference role: style/proportion anchor, not a likeness.** Generic game-protagonist
  human biped, athletic build, in the spirit of that reference. Generic face — no specific
  likeness intended, no face reference was given, so Form A ships with a generic
  Arkham-hero-adjacent face per the original fallback in this section.
- **Costume is modeled in, not a nude base.** Simple shirt + pants, matching the reference's
  henley-shirt-and-cargo-pants silhouette. The reference photo also shows Drake-specific
  loadout gear (shoulder holster, ammo pouches, rope) — that gear is NOT part of this spec;
  "simple shirt/pants" means the plain garments only. Costume is sculpted into the base mesh
  as its own read (not a separate cloth-sim layer), since the brief calls it "costume
  modeled in."
- **Coverage gap: no full body, no legs, no back view in the reference.** Anywhere the
  reference doesn't show (legs, feet, back, exact face), standard human biped proportions
  apply, at the already-locked 188 cm total — i.e. the §3 numbers (thigh ~47 cm, shin
  ~49 cm, shoulder width ~50 cm, arm length ~64 cm) govern, not a guess extrapolated from
  the photo crop.
- A cropped single-subject front view was prepared from the contact sheet for generation
  input: `design/refs/form-a-front-crop.png` (the centered front-facing panel, head to
  mid-thigh, plain black background).

Design is approved. Building starts now, in installments — see `build-plan.json`.
