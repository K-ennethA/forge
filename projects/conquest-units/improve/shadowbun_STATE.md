# shadowbun STATE (updated 2026-10-09)

- Version: v1.2 (2026-10-09): v1.1 ACCEPTED by the artist except the eyes -> EYE_AZ 58 -> 34 (markup design/reference/shadowbun-v11-eye-annotation.png); arm nubs accepted
- Spec: design/review-log.md "2026-10-09 — NEW UNIT: Shadowbun" + "Shadowbun answers" + design/reference/shadowbun-character-sheet.webp (intake: design/reference/shadowbun/intake.json; true orthos = back + side, the front is 3/4)
- Pattern: firefly (from-scratch SDF shell + closed parts; templates/new_unit/BUILD_CONTRACT.md)
- v1.2 numbers: DIGEST ae4d2894799010a9 (twin except bake_normal, 1 texel, gate PASS); checks 6/7 + 10/11 (cell_fit report-only); 18,400 tris; 0 open edges
- LOCKED (artist answers 2026-10-09): floaty waddle walk; two lateral eyes; small glow on eye + grin (emission_scale 0.4, peak 0.359); thick front from the back view
- Knobs that matter: EGG_PROFILE / EGG_WIDTH, MUZZLE_*, TUFT_LEN / TUFT_FLOW / TUFT_R, TAIL_*, BELLY_* / SCALLOPS, EAR_*, EYE_*, TEETH, WADDLE_ROLL / LIFT / SWING, BREATH / EAR_TWITCH / TAIL_IDLE, TAIL_PUFFS / TAIL_RIM / PUFF_K, EAR_FRINGE / EAR_CUP, TUFT_K / TUFT_R / TUFT_LEN (the arm-nub residual)
- Known: the build registers to intake floor y 767 / axis 905 on the side view (the drawn near sole sits ~15 px lower: dark fur vs dark sheet + the drawing's perspective); CORNER_CLAMP works around the polygonise zero-value hazard
- Open artist questions: OPEN-QUESTIONS 'Shadowbun' (B4 size -> lineup)
