# sand_drill_worm STATE (v2, 2026-10-12)

- Version: v1 = Pixal3D proxy (sha e1a05901, seed 56), LIGHT finish (proxy_finish.py; config improve/sand_drill_worm_finish.json). First unit on the light flow.
- Spec: design/review-log.md "2026-10-12 — NEW UNIT: Sand Drill Worm"; sheet design/reference/sand_drill_worm-character-sheet.png; intake design/reference/sand_drill_worm/intake.json.
- v2 (artist v1 verdict "yes only 2 arms, mouth dark red. teeth are fine"): the proxy's FORE drill pair cut (light.cuts, 41 capsules), the AFT pair kept; mouth interior repainted dark red (light.recolour, geometry mask, teeth kept). Otherwise geometry / texture / face as generated (no symmetrise, no repose). Floor at the drill-tail tip; x1.008 to the sheet front height.
- LOCKED: nothing yet (awaiting the artist).
- Knobs that matter: body_tris 18000 (window 8000-22000); light.dense head box density 2.5; light.uv_fix_overlap (angle 30); light.ao_fill_below 0.04; light.cuts; light.recolour (mouth stops / plane / depth); rig joints; clips block (idle 72 f, breathe 2 deg, walk_bob 0.03, walk_roll 4, arm_swing 8).
- Open for the artist: the aft drill bulbs carry less dark banding than the sheet (proxy texture as generated); a small flat notch where the stalk bends at the shoulder (the fore root's cut); movement + height (OPEN-QUESTIONS N14), clips PLACEHOLDER.
- Unit gates: digest twins, bake gate, contract checks (cell_fit report-only), compose GATE lines, proxy_finish.py gate + panel.
