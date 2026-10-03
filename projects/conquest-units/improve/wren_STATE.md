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
  angel-ring tint.

