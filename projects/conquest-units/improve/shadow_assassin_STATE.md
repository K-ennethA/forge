# Shadow Assassin - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v1 DRAFT (review-log 2026-10-05 "NEW UNIT: Shadow Assassin"; sheet design/reference/shadow_assassin/
  shadow_assassin_sheet.webp, 1536x1024). Runner improve/shadow_assassin_run.ps1 (~42 s headless: palette, build, renders,
  both checks, compare strip). No clips (S3 movement intent unanswered), no determinism twin yet (draft mode).
- **Numbers (v1):** 38,913 tris (budget 50k: 11,087 left; main 37,069 + blade 1,844). glb bc6b3c76ed1985bd, 11,401,272 B,
  54 joints, COLOR_0/COLOR_1/_GLOW present. Checks: improved 6/7 (cell_fit report-only: 1.853 m = hood top), rigged 8/11
  (+ clip_names/clip_loops by design) = the Elias exception set. flat_shaded 0 smooth faces (no hair exemption used).
- **Build:** MPFB body, NO FACE STACK (sheet: no face visible) - dark wrap + hood void instead; covered-skin cut 10,669
  faces. Heads-tall 7.03 by chin (= Elias 7.01). Garments TRUNK_HUNG (zero arm weight on cloak/skirts/belt/straps/hood);
  capes + shoulder plates shoulder-hung. Height default 1.80 m (S4, no sheet number).
- **Blade hold (baked into bind pose, clipless law):** one blade, right hand (S1; HAS_BLADE2 stubbed, errors if enabled).
  Wrist 26.8 deg after roll search (roll 280), elbow 54.9, 18 bones re-seated. Fingertip pads 1.0-3.0 mm, penetration 0.0.
  Knobs: BLADE_TILT fwd -35 (measured: +4 -> 57-77 deg wrist, -35 -> 27), GRIP_DIAG 10 (higher splays fingers), BLADE_GRIP
  (out 0.12 / fwd 0.06 / 0.48 below shoulder), per-finger curl solve fitted to the skinned mesh.
- **Palette:** 38 regions ALL pixel-sampled from the saved sheet (Elias v5 format, notes per region). Chips #302f32
  #494544 #5d5369 #837370 #ae8956. _GLOW: cloak_sigil + blade_sigil only, peak 0.12 (x2.0 emission = 0.24), hues 297/274;
  build asserts this gate. Renders use a sheet-paper backdrop (near-black unit vanishes on survey grey).
- **Known residuals (v1):** face reads as dark mannequin under the brim, not a pure void (brim 77 mm ahead of wrap; S5);
  scarf = stacked bands not a draped cowl; trousers painted-on not baggy; shoulder capes bulkier than sheet; side-view
  hood bulbous; small V-notch at hood opening top.
- **Open artist questions:** S1-S5 in design/OPEN-QUESTIONS.md (blade count, sigil glow tier, movement intent, height,
  hood void / eye-glint) + the v1 residuals verdict.
