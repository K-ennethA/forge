# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v2 (hair to the full Wren ribbon standard) on v1 = 50cdf85. Static build + rig, no clips.
  Spec: review-log 2026-10-02 "NEW UNIT: PROFESSOR ELIAS" + artist delta "hair ... match the style we decided on for wren".
  Runner: improve/elias_run.ps1 (~90 s; renders / probes written with the $VER prefix, now elias_v2_*; v1 stills = baseline).
- **LOCKED:** nothing yet (awaiting the verdict on renders/elias/elias_v2_sheet.png + elias_v2_hair_compare.png).
- **Hair (v2):** 25 scalp locks radiating from one crown whorl (HAIR_WHORL 174/62), front locks ending PAST the receding
  hairline (no cap rim); beard = 24 narrow locks in 3 layered lengths with real thickness (BEARD_T 3.8-4.6 mm) rooted on
  the under-beard zone's edge + dark beard core; 4 mustache locks; 13-19 stations; Wren layer resolve (4 rounds), tuck
  shade on every lock, angel ring, dark inner cap (Wren ratio), own UV strip, proxy bake per group (scalp / beard) leaned
  45 % per-lock; follow-through chains hair_front / hair_side.L/R / hair_back / beard.L/C/R (3 bones each, unkeyed).
- **Key knobs (top of elias_build.py):** HAIR_WHORL / HAIR_LOCKS / CLUMP_ROOT / HAIR_KIND_W / HAIR_LIFT, BEARD_LOCKS /
  BEARD_T / BEARD_LEN / BEARD_POINT / BEARD_ZONE / BEARD_CORE_*, MUSTACHE*, MACRO / TARGETS, BROW_W, FACE_LINES, GLASSES,
  MANTLE_* / CAPELET / CREST, SATCHEL, STAFF, BOOK, CRAVAT; palette palettes/elias/default.json.
- **Numbers (v2):** 49.1k tris (hero 30-50k: 0.9k headroom); 5.96 heads (Wren 5.94); mouth v_ratio 0.274, w/eye 0.687;
  iris 56.6 %; staff roll 240 / wrist bend 43.8 deg; hair top-sheet crossings past roots 59 pairs / 685 tri (12.7 per lock;
  Wren 12.0); 76 bones.
- **Known residuals:** face probe under-eye flatness 5.5 mm (1.3-2.4 mm without the painted age lines: age-macro lids);
  dark cap glimpsed under lifted side / front tips (Wren's inner-cap look, darker than v1); robe shows MPFB torso relief;
  fingers through the tome; cell_fit fails on the 1.86 m staff (report-only); the checker crashes on clip-less rigs (runs
  on improved/); beard core is rigid on the head (not on the beard chains).
- **Open artist questions:** skin tone; eye colour; beard length / shape + cravat visibility; mustache droop; glasses lens +
  frame colour; staff scale / hold; tome rest place; hair volume on top; age lines; movement intent before clips.
