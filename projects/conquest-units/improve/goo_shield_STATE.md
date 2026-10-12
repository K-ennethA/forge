# goo_shield STATE (scaffolded 2026-10-10)

- Version: v3 (2026-10-10: artist - helmet hugs the goo (half-width 0.993 = goo 0.843 + 0.15), brow sweep over the eyes, tab bottom z 3.07, craggy rock: 180 facets, lumps, 24 rim chips). DIGEST build=0b4df1ff299d5f14 identical=True. 15,428 tris. Earlier v2: helmet side flaps removed; centre V extended into a thick rounded nose-guard tab, bottom z 3.27 = eye-bottom line, 0.26 proud). DIGEST build=ad2f9e876f96fa7f identical=True. Earlier gate pass: opaque goo (GOO_TRANSLUCENT False), mid-lime below MID_Z 0.72 + lobes per the measured sheet split, rim RIM_Z 0.16). DIGEST build=d9ccd9df45a8ed2b identical=True. Checks 7/1 + 11/1 failed (cell_fit only). 14,328 tris; 0 open edges. Remaining: helmet crown 0.25 above the side drawing; armour corner gaps (back vs side contradict). Side view faces image-RIGHT (render.py ortho_side flipped). Clips PLACEHOLDER.
- Spec: design/review-log.md entry '2026-10-10 — NEW UNITS x2: Goo Shield, Fogeist' (section GOO_SHIELD) + design/reference/goo_shield-character-sheet.* (intake: design/reference/goo_shield/intake.json)
- Pattern: firefly (from-scratch SDF shell + closed parts; templates/new_unit/BUILD_CONTRACT.md)
- LOCKED: (nothing yet)
- Knobs that matter: (the build's TUNABLE CONSTANTS block; list the artist-facing ones here)
- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate
- Open artist questions: design/OPEN-QUESTIONS.md section 'Goo Shield / Fogeist' (N11)
