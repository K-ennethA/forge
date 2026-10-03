# Varden - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v3 DRAFT (shoulders) on v2 (27b3feb; v1 5e0798c). Static, no clips. Spec: review-log 2026-10-02 "NEW UNIT: General Varden" + artist delta 2026-10-03
  "make this character taller and a broader/wider frame. wider shoulders." + 2026-10-03 "wider shoulders still". Runner: improve/varden_run.ps1 (~90 s; $VER varden_v3, base varden_v2;
  -> renders/varden/varden_v3_sheet.png + varden_v3_compare.png, same fixed camera both sides via render --fixed). Glb always on (export_glb._GLOW path).
- **LOCKED:** nothing yet (awaiting the verdict on the v3 sheet + compare strip).
- **v2 frame:** FRAME_TARGETS (body-only MPFB dials: shoulder dist, torso horiz / vshape / depth / dorsi / pectoral / vert, deltoids, upper arms,
  upper / lower leg height + thigh muscle, neck circ; v3: shoulder dial at its 1.0 max, torso horiz 0.80 / vshape 0.85, deltoids 0.80)
  + SHOULDER_WIDEN (0.015 m / side, clavicle k 0.55: geometric arm-chain shift past the exhausted dial, s1) + BODY_SCALE 0.95203 fixed = v1 head size kept (macros unchanged). Face verts vs v1 (build frame,
  eye-aligned): p99 0.08 mm. All garments / fur / cloak / sword hang / rig re-fit by the generators (no garment knob changed).
- **Stack:** Elias v3 (= Wren house style); Varden deltas: swept-back top layer rooted on the hairline (HAIR_SWEEP, HAIR_ROOT_FLAT, CAP_RIM_PAINT),
  short crop (BEARD_LEN / BEARD_POINT / BEARD_JAW_RISE / BEARD_TIP_TURN / BEARD_ENV_EAR_Y), BEARD_GREY streaks, FACE_SCARS + frown lines,
  FUR MANTLE = under-fur roll + 92 lens-section tufts (FUR / FUR_ROWS / FUR_TUFT), GLOVE_DECIMATE 0.40, UV winding fix (s6).
- **Key knobs (top of varden_build.py):** FRAME_TARGETS / BODY_SCALE, MACRO / TARGETS (face), BROW_*, FACE_LINES / FACE_SCARS, BEARD_* / MUSTACHE,
  HAIR_SWEEP / HAIR_LOCKS / HAIR_LOOSE, FUR*, CLOAK_*, CREST, SKIRT_* / TABARD, COLLAR, VAMBRACE, BOOT_STRAPS, BROOCH / CHAIN / STRAP,
  SWORD / SCABBARD / SWORD_HANG / SWORD_TILTS; palette palettes/varden/default.json (sheet-sampled).
- **Numbers (v3):** 48.8k tris (hero 30-50k); height 1.927 m (v1 1.81); shoulder joint span 0.563 m (v2 0.491, v1 0.391), outer width 0.719 m (v2 0.641, v1 0.522);
  6.47 heads (v1 6.07; accepted for this character); mouth v 0.270, w/eye 0.700; iris 61.6 %; resolve 43 pairs / 372 tri past roots (6.3 per lock;
  Wren 12.0); 59 locks; 76 bones; sword hang tilt 2 / -10 deg (off the search edge), roll 250 / wrist bend 48.6 deg (guard hold).
- **Known residuals:** cheek beard locks read as vertical strips; thin dark crease behind the hairline (3/4); mustache spine kink 149 deg (must.L1);
  face probe flatness L 1.81 / R 3.56 mm (v1 2.36 / 1.99) with the face verts unchanged; brow inner 0.565 x eye width (heavy by spec);
  cell_fit fails (1.96 m, report-only); guard-hold wrist bend 48.6 deg.
- **Open artist questions:** scar placement / count; beard grey-streak pattern; fur mantle volume; sword at hip vs drawn; cloak length (hem kept 0.24 m);
  HOW HE MOVES (before clips); eye colour (sampled grey-hazel); is the 0.56 m shoulder-joint span (v3) enough.
