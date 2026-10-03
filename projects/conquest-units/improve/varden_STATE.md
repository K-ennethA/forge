# Varden - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v1 DRAFT (uncommitted). Static, no clips. Spec: review-log 2026-10-02 "NEW UNIT: General Varden"; sheet design/reference/varden/varden_sheet.webp.
  Runner: improve/varden_run.ps1 (~115 s; build -> renders + face probe + hair diag + checker -> renders/varden/varden_v1_sheet.png). Glb always on (export_glb.add_glow_attr + EXPORT_KW).
- **LOCKED:** nothing yet (awaiting the verdict on renders/varden/varden_v1_sheet.png).
- **Stack:** Elias v3 (= Wren house style): s1 verbatim; ribbon locks, layer resolve, tuck shade, own UV strip, per-group proxy bake (scalp / beard).
  Varden deltas: swept-back top layer ROOTED on the hairline (HAIR_SWEEP, flat root edges HAIR_ROOT_FLAT, rim painted root tone CAP_RIM_PAINT; no hairline row);
  short crop (BEARD_LEN 20 mm, BEARD_POINT 0.30, jaw-line tips BEARD_JAW_RISE, tips seeded by azimuth BEARD_TIP_TURN, ear-free envelope BEARD_ENV_EAR_Y);
  beard tones + grey streaks by whole locks (BEARD_GREY); painted scars FACE_SCARS + glabella frown lines; FUR MANTLE = under-fur roll + 92 lens-section tufts (FUR / FUR_ROWS / FUR_TUFT).
- **Key knobs (top of varden_build.py):** MACRO / TARGETS (square jaw, heavy brow), BROW_*, FACE_LINES / FACE_SCARS, BEARD_* / MUSTACHE, HAIR_SWEEP / HAIR_LOCKS / HAIR_LOOSE,
  FUR*, CLOAK_*, CREST, SKIRT_* / TABARD / SKIRT_STARS, COLLAR, VAMBRACE, BOOT_STRAPS, BROOCH / CHAIN / STRAP, SWORD / SCABBARD / SWORD_HANG / SWORD_TILTS, GLOVE_DECIMATE; palette palettes/varden/default.json (sheet-sampled).
- **Numbers (v1):** 48.2k tris (hero 30-50k); 6.07 heads; mouth v 0.270, w/eye 0.700; iris 61.6 %; resolve 46 pairs / 416 tri past roots (7.1 per lock; Wren 12.0);
  59 locks; 76 bones; sword roll 250 / wrist bend 48.6 deg (guard hold); face probe flatness 2.36 / 1.99 mm (painted lines).
- **Known residuals:** cheek beard locks read as vertical strips; thin dark crease behind the hairline (3/4 view); mustache spine kink 149 deg (must.L1);
  sword hang search ends at its limit tilt (2 / -14 deg); brow inner 0.565 x eye width (heavy by spec, house 0.27); cell_fit fails (1.84 m, report-only); gloves decimated (6512 -> 2604 faces).
- **Open artist questions:** scar placement / count; beard grey-streak pattern; fur mantle volume; sword at hip vs drawn (grip recorded, sheathed rest); cloak length; HOW HE MOVES (before clips); eye colour (sampled grey-hazel).
