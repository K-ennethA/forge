# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v4 DRAFT (research H5 + H8: vertex-normal hair carrier + LONG beard shell) on v3 (hair coverage fix). Static, no clips.
  Spec: review-log 2026-10-02 "NEW UNIT: PROFESSOR ELIAS" (full grey beard + mustache, groomed masses) + design/research/hair-face-best-practices.md H5 / H8.
  Runner: improve/elias_run.ps1 (~85 s; $VER elias_v4, ELIAS_BASE elias_v3; glb always (shared _GLOW path); checker on improved AND rigged;
  -> renders/elias/elias_v4_sheet.png + elias_v4_hair_compare.png = portrait / face 3/4 / chin-up / front / face 3/4 hair one-tone, v3 | v4).
- **LOCKED:** nothing yet (awaiting the verdict on the v4 sheet + strip: the "groomed masses" read of the shell).
- **v4 hair carrier:** HAIR_NORMAL_CARRIER "vertex" for the whole hair system (Wren f1dcc74 pattern): proxy-leaned normals as custom split
  normals on hair_* faces, map bake skipped (strip flat); shells lean HAIR_SHELL_NORMAL_MIX 0.85 to their own normals. Decode p90 44.9 deg
  (v3 map, representable; 59.2 all) -> carry p90 0.0041 deg; shading jump across 11.8k hair edges 0. conquest_smooth_regions = hair family.
- **v4 beard:** 24 locks + core + 6 mustache locks REPLACED by two conforming shells (s5: Varden's column builder, long variant): zone top edge
  sunk -> thickness ramp (4.5 cheek / 9.5 mm chin) -> below the chin a hanging mass over the DROP envelope (cravat / chest / capelet +
  hang_off 6 mm, taper 0.20 / 0.35) -> LOBED hem (BEARD_HEM U, 75 mm front; lobe joins 8/23/40/60/80 deg = 7 hanging lobes + jaw masses,
  sine-rounded, grooved 5 mm, belly 2 mm) -> underside onto the neck (sunk, clear of the cravat). Mustache shell 2 lobes / side, no teeth,
  no crevice paint. Regions hair_beard* (grey = the hair tones); zone lower edge neck_rise; stubble fade beard_fade1-3 (s2, painted by centroid);
  beard_inner lightened to the fade's first tone. Chains beard.L/C/R KEPT, re-anchored on the shell's hanging mass ("beard_shell" weights,
  head above BEARD_CHAIN_LEAVE 10 mm over the chin; 76 bones).
- **v4 knobs (top of elias_build.py):** BEARD_LEN, BEARD_HEM, BEARD_SHELL (offset / offset_psi / sink / ramp / hang_off / taper / col_deg /
  lobe_edges / lobe_len / lobe_round / lobe_jitter / groove_depth / groove_from / lobe_belly / rows / tip_f / crevice_f), BEARD_ZONE neck_rise,
  BEARD_FADE, BEARD_CHAIN_PSI / BEARD_CHAIN_LEAVE, MUSTACHE_SHELL, HAIR_NORMAL_CARRIER, HAIR_SHELL_NORMAL_MIX; palette hair_beard* / beard_fade*.
- **Key knobs (unchanged):** HAIR_WHORL / HAIR_LOCKS / CLUMP_ROOT / HAIR_KIND_W / HAIR_LIFT, MACRO / TARGETS, BROW_W, FACE_LINES, GLASSES,
  MANTLE_* / CAPELET / CREST, SATCHEL, STAFF, BOOK, CRAVAT; palette palettes/elias/default.json.
- **Numbers (v4):** 46.5k tris (v3 49.8k: 3.5k headroom BANKED); beard+mustache 3,432 (shell 2,952 + mustache 480) vs v3 6,706 (locks 6,358 +
  core 348); body 15,583 (zone re-cut; v3 15,623); scalp locks + cap byte-identical to v3 (s5 scalp digest 8b1fccca837e1101, same centre
  shift; hair-strip UVs repacked); 5.96 heads; zone visibility probe (21 views): 168 beard_inner faces / 14.7 cm2 visible, 35 deeper than 3 mm.
- **Known residuals:** shell top edge reads as a thin bright ledge on the cheek line; thin pale strip at the sideburn->jaw back edge (sunk end
  columns, 3/4 + side); hem tip band + groove lines read a little like fingers from straight below/front; 50 side columns lengthened to the
  zone's lower edge (neck_rise could rise further); plus v3's: narrow dark gaps between hairline tips, under-eye flatness, MPFB robe relief,
  fingers through the tome, cell_fit on the 1.86 m staff (report-only). Rigged check: clip_names / clip_loops fail (no clips, by design).
- **Open artist questions:** beard length / lobe shape (now 75 mm, 7 hanging lobes) + cravat visibility (hidden under the beard, band shows at
  the neck sides, not forced); skin tone; eye colour; mustache droop; glasses lens + frame colour; staff scale / hold; tome rest place; hair
  volume on top; age lines; movement intent before clips.
