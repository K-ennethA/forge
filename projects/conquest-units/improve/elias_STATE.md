# Elias - unit state (orchestrator-maintained; briefs point here instead of restating history)

- **Current:** v5 DRAFT (sheet-matched SCALP, review-log 2026-10-03 "Elias SHEET SAVED + scalp hair rejected") on v4 (vertex-normal hair
  carrier + LONG beard shell). Static, no clips. Judge = design/reference/elias/elias_sheet.webp (front / side / back + head panel).
  Runner: improve/elias_run.ps1 [-Twin] (~100 s; $VER elias_v5, ELIAS_BASE elias_v4; glb always; checker on improved AND rigged; -Twin =
  scratch twin build, glb + textures + combined digest compared) -> renders/elias/elias_v5_sheet.png + elias_v5_hair_compare.png =
  v4 | v5 | SHEET for headc_front / headc_tq (his right, = head panel) / headc_side (his left) / headc_back + one-tone 3/4 column.
- **LOCKED:** nothing yet (awaiting the artist's verdict on the v5 strip: the scalp style + the sampled palette).
- **v5 scalp:** HAIR_MASSES = 6 primary masses, 48 ribbon locks, widths 14..64 mm hand-set (spread 4.57 overall, >= 3.0 in every mass):
  bang (6, hair_front: long bangs from the side part across the forehead to his right temple), fall (5, hair_side.L: the short side down
  his left temple past the outer rim), sidel (7) / sider (8, hair_side.L / .R: swept-back waves flaring over the ears, under-layers
  beneath), crown (10, rigid: top sweep from the part toward his right/back + flicks), back (12, hair_back: from the whorl, flaring at the
  nape). HAIR_PART (his left, front hairline -> whorl 150/62); every non-back lock roots ON the part (PART_ROOT_K 0.60). Cap thickened
  HAIR_CAP_T 5 -> 12 mm (feather 30 mm) so the voluminous locks rest on it; cap clearance pushes ignore cap RIM faces (23 mm spike fix).
  Scalp built AFTER the beard/mustache shells (they are in BVH_HAIR); GLASSES moved verbatim s6 -> s5 so clear_spine keeps off them
  (BVH_HAIR_CLR, wire inflated GLASSES_HAIR_CLEAR 2.5 mm). v4 HAIR_LOCKS / HAIRLINE_LOCKS / whorl flow removed.
- **v5 palette:** skin / skin_shadow / eyes (iris, iris_dark, lash, brow) / hair family / beard family PIXEL-SAMPLED from the sheet
  (band medians, notes say SAMPLED vs derived); fades / lips / mouth / liner / face_line / inner / crevice derived by the v1-v4 rules.
- **Key knobs (top of elias_build.py):** HAIR_MASSES (per lock tier / width / part origin / tip / lift x), HAIR_PART, HAIR_WHORL,
  PART_ROOT_K, HAIR_LIFT_RAMP, BANG_TIP_OFF / BANG_AIM_EL / BANG_SWEEP, GLASSES_HAIR_CLEAR, HAIR_CAP_T / HAIRLINE_FEATHER; v4 beard knobs
  (BEARD_*, MUSTACHE_SHELL) and HAIR_NORMAL_CARRIER / HAIR_SHELL_NORMAL_MIX unchanged; palette palettes/elias/default.json.
- **Numbers (v5):** 49,339 tris (v4 46,463; budget 50k: 661 left); scalp locks 8,924 (v4 6,048); cap 1,740; beard digest 3bf848d4b1d8600d
  (= v4 shells); 89 non-hair parts + painted body byte-equal to v4. Cap exposure (37 views) 23.5 % (v4 24.8 %); hair <-> glasses 0 tri
  pairs, 7.2 mm min gap; hair <-> beard 0 pairs. Hair diag: kinks p50 13.1 (v4 12.0), top-sheet tri pairs / lock 40 (v4 37). Carry p90
  0.0043 deg. Twins byte-equal (glb ced4306f81a0cf75, 10,730,836 B).
- **Unit gates beyond the contract:** HAIRCLEAR (locks vs glasses / beard), CAPEXPOSE (vs v4 24.8 %), BEARDDIGEST (= v4), MASSES spread.
- **Known residuals:** paint tiers (ring / tuck) still read as per-segment patches on the wide locks; the crown flick roots bunch into a
  small knob at the whorl (back view); cap shows at both temples under the side hair (as v4); sidel.1 tip pushed 5.5 mm by the cap
  clearance; sampled beard tone sits close to the skin luminance; plus v4's beard / face / prop residuals. Rigged check: clip_names /
  clip_loops fail (no clips, by design); cell_fit report-only (staff).
- **Open artist questions:** the v5 scalp read vs the sheet (part side, bang length, side flare, volume); beard tone (sampled, lighter
  than the hair) vs the skin; beard length / lobe shape + cravat visibility; mustache droop; glasses lens + frame colour; staff scale /
  hold; tome rest place; age lines; movement intent before clips.
