# Varden - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v4 DRAFT (beard shell, research #2 H9) on v3 (shoulders; v2 27b3feb, v1 5e0798c). Static, no clips. Spec: review-log 2026-10-02 "NEW UNIT: General Varden" + artist deltas 2026-10-03 (taller / wider shoulders) + design/research/hair-face-best-practices.md H9/H8 (BEARD_SHELL / BEARD_CROP). Runner: improve/varden_run.ps1 (~90 s; $VER varden_v4, base varden_v3;
  -> renders/varden/varden_v4_sheet.png + varden_v4_compare.png = portrait / face 3/4 / chin-up (portrait_low) / fixed front, v3 | v4). Glb always on (export_glb._GLOW path).
- **LOCKED:** nothing yet (awaiting the verdict on the v4 sheet + compare strip: the cheek-strip read).
- **v2 frame:** FRAME_TARGETS (body-only MPFB dials: shoulder dist, torso horiz / vshape / depth / dorsi / pectoral / vert, deltoids, upper arms,
  upper / lower leg height + thigh muscle, neck circ; v3: shoulder dial at its 1.0 max, torso horiz 0.80 / vshape 0.85, deltoids 0.80)
  + SHOULDER_WIDEN (0.015 m / side, clavicle k 0.55: geometric arm-chain shift past the exhausted dial, s1) + BODY_SCALE 0.95203 fixed = v1 head size kept (macros unchanged). Face verts vs v1 (build frame,
  eye-aligned): p99 0.08 mm. All garments / fur / cloak / sword hang / rig re-fit by the generators (no garment knob changed).
- **Stack:** Elias v3 (= Wren house style); Varden deltas: swept-back top layer rooted on the hairline (HAIR_SWEEP, HAIR_ROOT_FLAT, CAP_RIM_PAINT),
  short crop (v4: BEARD_SHELL / MUSTACHE_SHELL / BEARD_FADE, below), BEARD_GREY streaks, FACE_SCARS + frown lines,
  FUR MANTLE = under-fur roll + 92 lens-section tufts (FUR / FUR_ROWS / FUR_TUFT), GLOVE_DECIMATE 0.40, UV winding fix (s6).
- **v4 beard:** the 27 beard locks + core + 6 mustache locks REPLACED by two conforming shells (s5 BEARD_SHELL column builder: zone top edge sunk -> thickness field -> hanging envelope -> serrated hem -> underside onto the neck, sunk; MUSTACHE_SHELL same builder over the lip, ends sunk into the beard) + the skin STUBBLE FADE (s2 BEARD_FADE: 3 steps painted by face centroid, serrated; no cuts). Beard regions renamed hair_beard* (contract smooth cap = "hair" prefix); fade steps beard_fade1-3 (skin family). Shells rigid:head -> beard chains gone (76 -> 67 bones). HAIR_NORMAL_CARRIER "vertex" wired for the WHOLE hair system (Wren pattern); shells lean HAIR_SHELL_NORMAL_MIX 0.85 to their own normals.
- **v4 knobs:** BEARD_SHELL offset (3.2 / 5.8 mm cheek / chin), sink 0.6 mm, ramp 12 mm, taper (0.90, 1.25), edge serration tooth_w 12 mm x cols_per_tooth 3, tooth_len 4.5 mm x jitter 0.8, groove 0.30, rows (9, 3); BEARD_LEN 12 mm (was 20), BEARD_JAW_RISE / BEARD_JAW_PSI kept; BEARD_ZONE neck_rise (30 mm over |x| 22 -> 56 mm); BEARD_FADE fade band width 8 mm (zone-field metres), 3 steps, serration 3 mm / 6 mm teeth; BEARD_GREY chin (8 deg, from row 0.55) + streaks (+-42, +30 deg); MUSTACHE_SHELL offset 4.5 mm, tip_x 39 mm, tip_drop 0.34, 2 lobes / side; BEARD_ENV_EAR_Y 0.005 (was 0.012); HAIR_SHELL_NORMAL_MIX.
- **Key knobs (top of varden_build.py):** FRAME_TARGETS / BODY_SCALE, MACRO / TARGETS (face), BROW_*, FACE_LINES / FACE_SCARS, BEARD_ZONE / BEARD_SHELL / BEARD_FADE / BEARD_GREY / MUSTACHE_SHELL, HAIR_NORMAL_CARRIER / HAIR_SHELL_NORMAL_MIX,
  HAIR_SWEEP / HAIR_LOCKS / HAIR_LOOSE, FUR*, CLOAK_*, CREST, SKIRT_* / TABARD, COLLAR, VAMBRACE, BOOT_STRAPS, BROOCH / CHAIN / STRAP,
  SWORD / SCABBARD / SWORD_HANG / SWORD_TILTS; palette palettes/varden/default.json (sheet-sampled).
- **Numbers (v4):** 45.1k tris (v3 48.8k; beard+mustache 2,432 vs 5,792 locks; body 14,234 vs 14,226); height 1.927 m; shoulder joint span 0.563 m, outer width 0.719 m;
  6.47 heads; mouth v 0.270, w/eye 0.700; iris 61.6 %; scalp locks + cap byte-identical to v3; hair diag (26 scalp locks) 37 pairs / 1403 tri, top sheets 34 / 325; 67 bones; sword hang tilt 2 / -10 deg, roll 250 / wrist bend 48.6 deg.
- **Known residuals:** beard back edge (sideburn -> jaw) is a straight vertical line + a thin strip of beard_inner at the sunk end column; mustache ends + the beard's lip-band step read as small brackets beside the mouth corners; thin dark crease behind the hairline (3/4);
  face probe flatness L 1.81 / R 3.56 mm (v1 2.36 / 1.99) with the face verts unchanged; brow inner 0.565 x eye width (heavy by spec);
  cell_fit fails (1.96 m, report-only); guard-hold wrist bend 48.6 deg.
- **Open artist questions:** scar placement / count; beard grey-streak pattern; fur mantle volume; sword at hip vs drawn; cloak length (hem kept 0.24 m);
  HOW HE MOVES (before clips); eye colour (sampled grey-hazel); is the 0.56 m shoulder-joint span (v3) enough.
