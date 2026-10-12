# leaf_bird STATE (scaffolded 2026-10-10)

- Version: v2 (2026-10-10: artist - crest removed, body slimmed to a leaf: BODY_KX 0.78->0.50, SPINE_RSCALE 0.80, max half-width 0.698->0.400). DIGEST build=9c8fdc2beac2f091 identical=True. 14,556 tris. (v1: build=3c2e5ec75813f717.) Checks 7/1 + 11/1 failed (cell_fit only). 15,878 tris; 0 open edges. Reconciled to the side view (H 4.21, belly at z 0). Front face is round, not the drawn V; no pupil (none drawn); back view lateral-leaves-down ignored (contradicts front). Clips PLACEHOLDER.
- Spec: design/review-log.md entry '2026-10-10 — NEW UNITS x3' (section LEAF_BIRD) + design/reference/leaf_bird-character-sheet.* (intake: design/reference/leaf_bird/intake.json)
- Pattern: firefly (from-scratch SDF shell + closed parts; templates/new_unit/BUILD_CONTRACT.md)
- LOCKED: (nothing yet)
- Knobs that matter: (the build's TUNABLE CONSTANTS block; list the artist-facing ones here)
- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate
- Open artist questions: design/OPEN-QUESTIONS.md section 'Petal Cobra / Crystal Canine / Leaf Bird' (N6, N7)
