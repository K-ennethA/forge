# Varden - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v5 DRAFT (sheet-matched SCALP, review-log 2026-10-04 "Compendium import of the 6; Elias staff/robe; Varden hair" item 3) on v4
  (beard shell 25e3207; v3 shoulders; v2 27b3feb, v1 5e0798c). Static, no clips. Judge = design/reference/varden/varden_sheet.webp
  (front / side / back + head panel). Runner: improve/varden_run.ps1 [-Twin] (~100 s; $VER varden_v5, base varden_v4; glb always; checker on
  improved AND rigged; -Twin = scratch twin, glb + textures + combined digest compared) -> renders/varden/varden_v5_sheet.png +
  varden_v5_compare.png = v4 | v5 | SHEET for headc_front / headc_tq (3/4, his right = head panel) / headc_side (his left) / headc_back + one-tone.
- **LOCKED:** nothing yet (awaiting the v5 scalp verdict). Non-scalp geometry = v4 byte-identical (NONSCALPDIGEST 8cccb8cd418e5c16, 218 parts + body).
- **v5 scalp:** HAIR_MASSES (build.py) = 5 masses / 48 ribbon locks, widths 13-60 mm (spread per mass >= 3.07, all 4.62): front 12 (hair_front;
  rooted ON the painted hairline via hl_dir + HL_ROOT_ABOVE, swept up + back, asymmetric: the big mass to his right, wings over both temples,
  3 low-lift under-layers), sidel / sider 6 each (hair_side.L/R; temple hairline -> back ABOVE the ear, tips flicked out), crown 14 (rigid;
  from the top back over the back mass + back-top / top-side under-layers), back 10 (hair_back; from the whorl to nape points). Flat roots for
  hairline-rooted masses (HAIR_ROOT_FLAT). Cap 11 mm (feather 30 mm) + CAP_BULGE 12 mm round the front-top axis (fills under the swept-up front);
  CAP_CROP: low side / back outer cap painted hair_root (the short crop). Grey streaks: HAIR_GREY_MAP on 10 locks (sides 6, behind the ears 2,
  crown 2) -> new regions hair_grey (131,124,112 sampled: head-panel side hair) / hair_grey_tip (appended to REG + HAIR_REGS).
  s5 probes: MASSES, SCALPGREY, HAIRCLEAR (scalp vs beard), CAPEXPOSE (+ dark-inner share), SCALPDIGEST, BEARDDIGEST.
- **v5 rigged flat_shaded fix (s6 BODYSLIVER):** body faces < SLIVER_AREA collapsed after the non-scalp digest (16 found, 26 faces removed,
  13 verts merged, max move 0.93 mm): the float32 re-rounding under the armature swung 2 needle faces' normals; rigged flat_shaded green.
- **Numbers (v5):** 49,090 tris (v4 45,114; scalp locks 8,610 vs 4,608; cap 1,814; body 14,208 vs 14,234); hair top 1.983 m (crown 55.6 mm over
  the scalp, v4 30.6); CAPEXPOSE visible 50.5 % of cap area, dark inner 22.5 %; HAIRCLEAR 0 pairs / > 30 mm; carrier p90 0.0043 deg; chains
  3 bones each, 31-39 mm (short crop: s_leave past control 3); glb a4d5d5b0a65f3d10, 10,562,692 B.
- **Unit gates beyond the contract:** NONSCALPDIGEST (= v4), BEARDDIGEST fab8370c38294dd9, MASSES spread >= 3, CAPEXPOSE, HAIRCLEAR, tris <= 50k.
- **Known residuals:** EARS stay under the cap (s2 hair region covers the upper ear since v1 -- needs a hairline-around-the-ear lane, out of the
  scalp scope); dark notch at the top centre (front view) + dark under-arch patches at the back-top corners (CAPEXPOSE); HAIRPUSH max 15 mm at
  lock.front.3's flat root (the bulged cap); front locks still read as ribbed bands from straight ahead; twin raw bake_normal sha differed once
  under parallel load (shipped glb / PNGs byte-equal; a solo third build matched main); v4 residuals (beard back edge, mustache brackets,
  face probe, cell_fit report-only, wrist bend 48.6 deg).
- **Open artist questions:** the v5 scalp read vs the sheet (front volume height, tousle amount, grey streak placement); ears exposure;
  scar placement / count; beard grey-streak pattern; fur mantle volume; sword at hip vs drawn; cloak length; HOW HE MOVES (before clips); eye colour.
