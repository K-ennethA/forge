# Wren â€” unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v6.1 (lips + wider mouth) delivered on v7 base; see this commit.
- **LOCKED (artist-approved, do not regress):** body/outfit/clips below the neck (v1);
  eye SOCKET scale x1.30 ("perfect"); iris at ~58% coverage; flat under-eye (v5 fill);
  one unified mouth line at Ashe ratios (v6) + subtle lip volumes, paler tint, 44 mm width (v6.1); side part on his left; ribbon locks +
  layer resolve + tuck shade (v7); HAIRSTABLE decoupling (face/mouth edits must never
  move hair â€” 4-case runner gate).
- **Style bar:** FE/anime â€” refs in design/reference/fe-style/ (archer figure = hair
  + mouth bar; Ashe portrait = face ratios) and anime-3d/ (Alicia = construction).
- **Hair normals (research H5, 2026-10-03, pending commit):** HAIR_NORMAL_CARRIER="vertex" -- proxy-leaned normals ship
  as custom split normals on the hair regions (smooth), hair strip of the normal map flat; body/outfit flat and glb
  rows byte-identical to v6.1. Contract flat_shaded exempts only `conquest_smooth_regions` (hair-family, checker-capped).
  "map" restores the v6.1 bake. Paint tiers still read blocky (per-face paint, not shading).
- **Run clip (2026-10-03, pending commit; spec review-log "young hero sprint"; Wren is MALE):** "run" = 12 f / 0.5 s,
  240 steps/min (1 step = 2.08 game cells at 0.12 s/cell), stride 2.33 m @ 4.67 m/s, zero toe slip, flight f5-6 / f11-12,
  head stabilised, left-arm pump + cloak-wrap coupling, fork carried upright (walk convention). Knobs RUN_* / FORK_RUN
  (top of wren_build.py). idle/walk glb sampler data byte-identical to v6.1 (proved). Preview renders/wren/wren_run_preview.png
  (runner: render_runstrip + compose --run-preview). Contract clip_names needs "run" in rigkit.ALLOWED_CLIP_NAMES (outside
  the lane). KNOWN: run torso_outfit cloak crossings 24 = the s7 sash weight defect (12 sash verts/part at 1.0 on the
  forearms; the strand is visible in the approved walk too) -- s7 fix queued, not a clip issue.
- **s7 weight-source lane (2026-10-03, STOPPED - needs s3):** knobs TORSO_HUNG / CLOAK_FOREARM / CLOAK_HIP added to
  wren_build.py + s7, shipped OFF (build digest c9215939245d39b5 reproduced, every part). With them ON: every cloak crossing
  0 in idle / walk / run (run torso 24 -> 0), keys byte-identical, sash stretch strand gone -- BUT the root cause is s3
  GEOMETRY: BVH_SASH (comb_bvh, whole body incl. the A-pose hands) shapes the sash rings and places the pouch, so one ring
  per wrap sits at the rest hand (344-380 mm radius vs ~140-180) and the pouch sits ON the hand (x 0.32-0.40, phi 58).
  Trunk weights leave a rope spike + a pouch floating at the rest hands (renders/wren/wren_s7fix_walk6_before_after.png);
  v6.1 hides it by gluing them to the forearm / thumb_02_l (the pouch rides the hand to the chest in the run). Next: s3
  profile against the trunk (touches the LOCKED outfit look: the pouch moves onto the hip -- artist call), then retune.
- **Key knobs:** HAIR_NORMAL_CARRIER, EYE_SCALE, LIP_* / MOUTH_* (seal, smirk, smooth, proxy), RIBBON_* /
  LAYER_* (hair), HAIR_INTERIOR_R, NEAREST_TIE. All top-of-file in wren_build.py.
- **Unit gates beyond the contract:** face probe (under-eye/mouth numbers), mouth
  probe (second-feature traces), hair diag (interpenetration/kinks), HAIRSTABLE,
  poke-through, hair-into-head. Runner: improve/wren_run.ps1 (~160-200 s battery).
- **Known residuals (accepted or queued):** corner specks at mouth-line ends
  (closeup-only); outer-corner under-eye remnant; paint tiers read blocky on locks;
  33 locks vs the figure's denser count (+45-lock version needs ~2.5k harvest â€”
  queued grown); fringe-on-skin discontinuity vs EYE_SCALE edits (queued grown);
  muzzle side-to-side wrap (open artist question).
- **Open artist questions:** smirk strength/side; under-lip shading absent (confirm);
  hair paint tiers vs one-tone; lock count increase; back-lock length; winter
  angel-ring tint; run: arm pump amplitude (capped by the cloak drape), cloak trail, fork carry (upright vs charging).
- **Stale items fixed 2026-10-03:** s8 conquest_look string follows HAIR_NORMAL_CARRIER; wren_run.ps1 reads the hair-normal
  bake pair only when this build wrote it; wren_render.py '--hair-normal-flat' retired.

