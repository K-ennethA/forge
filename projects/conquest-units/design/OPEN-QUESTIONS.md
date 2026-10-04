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
