# winged_cat STATE (scaffolded 2026-10-10)

- Version: v1 draft (2026-10-10, incl. one gate pass: one-broom tail + broad ears). DIGEST build=b0304b4529c560d8 identical=True. Checks: improved 7/1 failed, rigged 11/1 failed (cell_fit only, report-only). 17,922 tris (REGULAR [8000, 22000]); 0 open edges.
- Spec: design/review-log.md entry '2026-10-10 — NEW UNITS x4' (section WINGED_CAT) + design/reference/winged_cat-character-sheet.png (intake: design/reference/winged_cat/intake.json; orthos back + side, --cuts 465,790)
- Pattern: firefly (SDF shell: head + ears + torso + haunches + legs + collar; closed parts: wings, 12 tail blades, eye/pupil decals, fangs)
- LOCKED: (nothing yet - awaiting artist verdict)
- Knobs that matter: HEAD_R/HEAD_C, EAR_TIP/EAR_YAW/EAR_INNER, EYE_X/EYE_A/EYE_B/EYE_TILT, MOUTH_C/MOUTH_R/MOUTH_TOP, COLLAR_TILT/COLLAR_TUBE/COLLAR_LUMPS/COLLAR_FUZZ, WING_TIP/WING_TRAIL/WING_SCALLOP, TAIL_* (broom cone), FLUTTER, TAIL_SWISH, SHIFT_ROLL
- Deviations:
  - The sheet's side and back tails can't both hold: the broom's top reaches the collar band in back view (the sheet's apex is z 1.5), and its bottom edge sits at z 0.47 (the back view draws 0.2-0.45).
  - The side-view eye is 0.55 u forward of the sheet (face-panel spacing kept).
  - The forehead is up to 0.18 u outside the egg-shaped head.
  - Not modelled: the ink outline, the "w" mouth line, the nostril dot.
  - The wing veins are faint.
- Clips: idle (breathing + wing flutter + tail swish) and walk (sitting weight-shift) are PLACEHOLDER.
- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate
- Open artist questions: design/OPEN-QUESTIONS.md section 'Soot Puff / Winged Cat / Phoenix Hatchling / Chest Monster' (N4, N5)
