# crystal_canine STATE (scaffolded 2026-10-10)

- Version: v2 (2026-10-10: artist - symmetric rest pose (mirror max 0.003), 23 pointed chest-fur locks to z 1.85, leg fur tufts, cupped hollow inner ears, flush gem inlay). DIGEST build=1bd10ac14009b411 identical=True. 18,412 tris. Earlier gate pass: clean V bib, plume tail with 4 locks, oval legs at drawn widths, drawn iris tone). DIGEST build=e8ea37cbb99063aa identical=True. Checks 7/1 + 11/1 failed (cell_fit only). 14,928 tris; 0 open edges. Remaining: lumpy bib; tube-like legs (no joint shaping); sparser mane (12/side); tail on the midline; the hind-leg stagger reconciled. Clips PLACEHOLDER.
- Spec: design/review-log.md entry '2026-10-10 — NEW UNITS x3' (section CRYSTAL_CANINE) + design/reference/crystal_canine-character-sheet.* (intake: design/reference/crystal_canine/intake.json)
- Pattern: firefly (from-scratch SDF shell + closed parts; templates/new_unit/BUILD_CONTRACT.md)
- LOCKED: (nothing yet)
- Knobs that matter: (the build's TUNABLE CONSTANTS block; list the artist-facing ones here)
- Unit gates: contract checks (cell_fit report-only), digest twins, bake gate
- Open artist questions: design/OPEN-QUESTIONS.md section 'Petal Cobra / Crystal Canine / Leaf Bird' (N6, N7)
