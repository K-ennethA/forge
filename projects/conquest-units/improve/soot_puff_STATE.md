# soot_puff STATE (scaffolded 2026-10-10)

- Version: v1 draft (2026-10-10). DIGEST build=74838a3bb0eb45d2, gate PASS (except-bake_normal, 1 texel). Checks: improved 7/1 failed, rigged 11/1 failed (cell_fit only, report-only). 17,998 tris (REGULAR [8000, 22000]); one closed shell, 0 open edges.
- Spec: design/review-log.md entry '2026-10-10 — NEW UNITS x4' (section SOOT_PUFF) + design/reference/soot_puff-character-sheet.webp (intake: design/reference/soot_puff/intake.json; axes pinned front 229 / back 639 / side 1009.5)
- Pattern: firefly (one SDF: ball + arms + seeded puffs; face = iso-cut ovals)
- LOCKED: (nothing yet - awaiting artist verdict)
- Knobs that matter: BALL_RXY/BALL_RZ, ARM_TOP/ARM_TIP/ARM_R, PUFF_BIG/PUFF_SMALL/PUFF_ARM, PUFF_SEED (7), PUFF_K, FUZZ_TAU (0.012), DUST_P (0.0: the sheet's views don't draw dusty crowns), EYE_*/MOUTH_*, SPECULAR (0), IDLE_LIFT, WALK_PITCH, BODY_TRIS
- Render fix: soot_puff_render.py ortho_side camera is on -X (this sheet's side view faces image-RIGHT).
- Deviations: built symmetric (the sheet's mouth sits 0.1 u right of the eye midline); the side drawing is ~0.1 u taller than the front (the build follows the front); the face sits at the silhouette edge in side view; no translucent "airy" wisps (puffs + normal map).
- Clips: idle (hover bob 0.03-0.30 u + arm sway) and walk (hover drift, 8 deg lean) are PLACEHOLDER.
- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate
- Open artist questions: design/OPEN-QUESTIONS.md section 'Soot Puff / Winged Cat / Phoenix Hatchling / Chest Monster' (N4, N5)
