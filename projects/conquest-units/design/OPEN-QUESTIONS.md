# Open questions / pending verdicts — handoff list (2026-10-04)

Every item ships with a default already in place; an agent picking one up changes the
named knob, reruns the unit's one-command runner, and shows renders. Law:
docs/lane-conventions.md. Per-unit facts: improve/<unit>_STATE.md.

## Wren (in-game as hero; all defaults live)
- W1 Fist tightness — knob HAND_POSES["fist"] (wren_build.py). Default FIST_CURL (80,100,35).
- W2 Run knee height — RUN_KNEE_DRIVE_DEG=25 (lower number = higher knee; 0 = old horizontal).
- W3 Idle stance width — LEG_TRACK["idle"]=1.06 (reads slightly "at attention"; raise to widen).
- W4 Run leg track — straight-under-hips now; midline runner's track = LEG_TRACK["run"] down.
- W5 Longer-stride walk clip (~1.2 m vs current 0.833 m) for a natural-cadence Sun/Moon walk
  pace — new clip work in wren_s8; game side reads stride from StoryRuleset automatically.
- W6 Pouch placement veto — now front-left hip (renders/wren/wren_pouch_look_front_tq.png);
  knob = pouch phi in wren_s3 (58; 70/80 measured worse for crossings).

## Elias (shipping to compendium as placeholder; v7.1 current)
- E1 Beard v6 verdict + knobs: clump count/size, 37.6 mm chin point length, cheek-beard
  height, mustache density (sheet reads fuller) — knobs in elias_build.py beard tables.
- E2 Grip height — STAFF_HOLD z 1.302 m (clean elbow) vs the sheet's shoulder-high fist.
- E3 Beard movement intent before any clips (chains exist: beard.L/C/R on the hanging mass).
- E4 Painted age lines keep/drop (v1 question, never answered) + under-eye probe residual.
- E5 Staff length 1.86 m vs the 1.8 m cell ceiling (cell_fit report-only; shorten or accept).
- E6 Tome placement: left hand / belt / satchel (currently left-hand prop, own bone).
- E7 Known smalls in elias_STATE.md: zone sliver above the mustache by the nose, beard tone
  close to skin brightness, tuck-shade off for beard (horizontal-bar artifact).
- E8 In-game faint glow: his _GLOW is not all zeros (body/staff peak ~0.17 teal — the orb
  and gem accents will faintly glow in-game). Intended look, or zero the non-orb regions?

## Varden (scalp rebuild in flight; ships to compendium after)
- V1 Heft verdict: 1.927 m, shoulder joints 0.563 m (next step = SHOULDER_WIDEN one-liner).
- V2 Scars: 2 right-cheek cuts + left-brow nick — placement/count per the sheet?
- V3 Beard grey pattern: chin-centre grey + 3 cheek tip-streaks — match the sheet?
- V4 Fur mantle: length/shagginess/tuft count (FUR, FUR_ROWS, FUR_TUFT knobs).
- V5 Sword: sheathed at hip (current) vs drawn idle; movement intent for clips.
- V6 Cloak hem 0.24 m off the floor; eye colour (sampled grey-hazel was a thin guess).
- V7 Ears: enclosed by the painted hairline since v1 (the cap covers the upper ear) —
  freeing them = redraw the hairline around the ear, own small lane (body digest changes).
- V8 v5 scalp smalls: front locks read ribbed head-on, dark notch at top centre, crown
  under-arch — verdict after seeing the renders; knobs in varden_build.py mass tables.
- V9 His _GLOW is all zero — the brooch's teal gem does NOT glow in-game (Elias's orb
  does). Should the gem get a glow tier in palettes/varden? One palette edit + re-export.

## Shadow Assassin (v1 draft in flight; sheet 2026-10-05)
- S1 Blade count: the sheet's front view arms one hand; back view silhouettes a blade at
  each side. Default = ONE blade, right hand, hold baked into the bind pose (clipless
  law); a second (left/sheathed) blade is a knob, not a rebuild.
- S2 Sigil glow: purple runes on the blade + the back-cloak diamond sigil — subtle _GLOW
  (Elias-orb tier, <=2.0 phone cap) is the default. Zero them, or brighter "shadow magic"?
- S3 Movement intent before any clips (shadow-step concepts are on the sheet's bottom
  strip): stalker crouch vs upright duelist idle? How does he enter/exit the smoke form?
- S4 Height: sheet gives no number (age unknown). Default 1.80 m — between Wren and
  Varden; the lithe read comes from the silhouette, not stature. Correct?
- S5 Hood void: default = face fully occluded (dark wrap + shadow, zero face stack per
  the sheet's "no face visible"). Confirm no eye-glint/emissive eyes wanted.

## Lyra (v1 draft in flight; sheet 2026-10-06)
- L1 Book hold: a 2-3 book stack cradled in one arm, baked into the bind pose (clipless
  law; the sheet's front + side views both show it). Which arm is canon — and should she
  ship empty-handed instead (Wren precedent: props only in combat/special clips)?
- L2 Height: age 17, "peer to Wren" — sheet gives no number. Default 1.70 m (a step under
  Wren). Correct?
- L3 Glow: no magic on the sheet — _GLOW all zero by default (Varden pattern). Or a faint
  tier on the gold compass emblem / diamond pin?
- L4 Ribbons: ponytail + necktie + ribbon tails get follow-through chains (hanging-mass
  law); the hairpiece tassel too. Any of these rigid instead?
- L5 Movement intent before any clips: studious/bookish idle (adjusting the book stack,
  glancing at pages) vs neutral academy stance? Any lore beyond the sheet notes?

## Seedant (v1 draft lane kicked off; sheet 2026-10-09)
- S1 Movement: ANSWERED 2026-10-09 — the placeholder plan is fine (idle sway / leaf rustle /
  arm taps / blink; walk = in-place pot hop). Knobs IDLE_* / WALK_* in seedant_build.py.
- S2 Game height: -> the SIZE LINEUP (all creatures in a row, queued); set relative sizes
  from it.
- S3 Pot texture: "subtle texture / scratches / worn edges" — v1 does worn lighter rim
  edges + tonal jitter; painted scratch strokes on the wall are a follow-up if wanted.
- S4 Skins: ANSWERED 2026-10-09 — alternate colours LATER; default only.
- S5 Pot shape: ANSWERED 2026-10-09 — ROUND (POT_DEPTH 1.0). Was: the sheet's side view draws the pot 0.70-0.76 of its front width, so v1 builds
  an OVAL pot (POT_DEPTH 0.74). Real pots are round: keep the oval (matches the sheet) or go
  round (POT_DEPTH 1.0)?

## Shadowbun (v1 draft lane kicked off; sheet 2026-10-09)
- B1 Movement: ANSWERED 2026-10-09 — FLOATY WADDLE walk (side-to-side rocking, soft hang time);
  idle as planned. Knobs WALK_* in shadowbun_build.py.
- B2 Eyes: ANSWERED — two, one on each side of the head.
- B3 Glow: ANSWERED — a small glow on the eye + grin (low tier, <= 2.0 cap).
- B5 Front: ANSWERED — no straight-on front on the sheet; reads as a THICK bunny (front width
  from the true-ortho back view).
- B4 Size vs the roster: -> the SIZE LINEUP (queued).

## Size lineup (DELIVERED 2026-10-09: renders/lineup/, design/size-table.json; rerun improve/lineup_run.ps1)
- Z2 ANSWERED 2026-10-09: sizes go in AS SPECIFIED (magmoo 8 ft included); wide units overhang one
  tile (Conquest e8974f2 law), no multi-cell. Was: Targets SET 2026-10-09 for 12 units (design/size-table.json). Three now outgrow one cell:
  magmoo 8 ft tall -> ~26 ft 10 in long; barkling 8 ft -> ~10 ft 11 in wide; petalfang 4 ft ->
  ~8 ft 11 in wide (cell 1.9 m / 6 ft 3 in; 2x2 boss cell 3.8 m / 12 ft 6 in). Multi-cell units,
  a different height measure (magmoo's head?), or tighter poses? Then the in-game apply lane.
- Z1 Relative sizes: today's in-game heights are NOT designed - they fall out of the cell fit
  (15 of 22 units at ~1.80 m incl. duskmaw / geode / mortis / firefly / seedant / shadowbun; 6
  footprint-bound units 0.47-1.58 m; only eldroot sized by role at 4.55 m). Set target_height_m
  per unit in design/size-table.json and rerun -> lineup_target.png. Applying targets in-game is
  a per-unit delivery change (the .import root_scale or roster model_scale) - a follow-up lane.
- S6 Seedant arms: ANSWERED 2026-10-09 — PLUMPER (x1.45 wide, ARM_AZ 44). Was: the sheet's front and side views place the arms in non-overlapping spots;
  v1.2 follows the front (side view: near arm ~0.4 forward of the drawing). Plumper arms =
  wider ARM_PATH + ARM_AZ ~44 (slightly more side offset). Keep, or try plumper?

## Bombee / Aquaoct / Canine Bat / Raven Rogue (sheets 2026-10-09)
(Artist rule 2026-10-09: the SHEET is the spec - nothing not drawn is asked or added; only movement + height
are asked, deviations from the sheet are reported.)
- N1 Movement intent per unit (defaults are PLACEHOLDER clips): Bombee hover + hover-drift;
  Aquaoct tentacle sway + shuffle-glide; Canine Bat idle + 4-beat trot; Raven Rogue cape-ruffle
  + hop-strut.
- N2 ANSWERED 2026-10-09: bombee 3 ft (floats), aquaoct / raven / pangolin 3 ft, canine bat 3'5", mole 2'6".

## Pangolin Knight / Shadow Mole (sheets 2026-10-09)
- M1 ANSWERED 2026-10-09: yes glow (faint pink tiers).
- N3 Movement for both (placeholder clips) + sizes (size table).

## Soot Puff / Winged Cat / Phoenix Hatchling / Chest Monster (sheets 2026-10-10)
- N4 Movement intent per unit (placeholder clips ship meanwhile): Soot Puff (floats on the sheet - hover?);
  Winged Cat (sits on the sheet - walks? flies?); Phoenix Hatchling; Chest Monster (tiny dangling legs).
- N5 In-game height of each.

## Petal Cobra / Crystal Canine / Leaf Bird (sheets 2026-10-10)
- N6 Movement intent per unit (placeholder clips ship meanwhile): Petal Cobra (slithers? hovers on its tail?); Crystal Canine;
  Leaf Bird (drawn diving - flies?).
- N7 In-game height of each.

## Eye Totem / Ember Eye (sheets 2026-10-10)
- N8 Movement intent (both float on the sheet - hover?) + in-game height of each.

## Ember Stump (sheet 2026-10-10)
- N9 Movement intent (a root-shuffle? hops?) + in-game height.

## Shield Rodent / Storm Vessel / Volcano Turtle (sheets 2026-10-10)
- N10 Movement intent per unit (Storm Vessel floats on the sheet - hover?) + in-game height of each.

## Goo Shield / Fogeist (sheets 2026-10-10)
- N11 Movement intent (Goo Shield slides like a slug? Fogeist drifts?) + in-game height of each.

## Lava Komodo / Spectormn / Halo Sentinel / Eclipse Wolf / Gemwing Dragon / Mystic Snow Spirit (sheets 2026-10-11)
- N12 Movement intent per unit (Spectormn + Halo Sentinel float on their sheets; Gemwing flies?) + in-game height of each.
- N13 RESOLVED 2026-10-11: Holly Snowman captured + intaken. Movement + height for it join N12.

## Game-side (Conquest; all behind debug keys, defaults live)
- G1 F7 look verdict: warm vs warm-neutralLUT vs new (WorldLook presets).
- G2 F8 feel verdict: free preset speeds now 2.2/5.0 m/s (artist bumped); camera
  pitch/FOV/distance knobs in OverworldFeel.PRESETS (S/M numbers are assumptions).
- G3 Eldroot height: 5.10 m ship spec vs the 3.8 m boss-footprint ceiling (raising one
  means raising both; note in eldroot.tres).
- G4 Designs needed from the artist: bastion, oakheart, undead (still on old models).
- G5 The 6 compendium placeholders need movesets/abilities/rebalance to become playable
  (roster .tres name their stat donors; CharacterSelect.EXCLUDED_IDS gates them).
- G6 NPC/wild walk-pace stride data (they may foot-slide at the new overworld pace).
- G7 Walk right-arm... superseded: both arms swing as of the posture rework. CLOSED.
- G8 Town buildings: ranked rebuild plan committed in survey/conquest_town_assets_audit.md
  (house family first, 1-3k tris per the world-feel research) — not started, next big lane.

## Sand Drill Worm (sheet 2026-10-12)
- N14 Movement intent (burrows? slithers upright? hops on its drill tail?) + in-game height.
