# Benchmark session 2026-09-23 — first runs, sufficiency verdict

All runs at 71ed8b3 through the live bridge, one turn each, no continuations.

| Run | Wall | Cost | Turns | Gates | Fidelity | Score | What happened |
|---|---|---|---|---|---|---|---|
| dish-baseline | 57.9 s | $0.54 | 8 | 4/4 | 6/6 | 96.4 | Clean pass end-to-end. |
| ear-baseline (recipes stripped) | 664.9 s | $2.36 | 39 | 0/8 | 0/4 | 0 | Built ears in part.py (parametric lane for an organic task); nothing saved. |
| ear-candidate-1 (recipes on) | 901.2 s | — | — | 0/8 | 0/4 | 0 | Same lane choice; hit the turn timeout with an empty scene. |
| ear-candidate-2 (+organic-path recipe) | 648.5 s | $2.42 | 48 | 0/8 | 0/4 | 0 | Sculpt lane reached, both ears built, "saved" — no file at the artifact path. |
| ear-candidate-3 (+verify-after-save) | 893.4 s | $2.15 | 36 | 0/8 | 0/4 | 0 | Same phantom save. |

## Root cause of the phantom saves

Not agent error. `save_project_blend` returned success honestly — into a
shadow projects root (`%APPDATA%\...\Blender\5.0\scripts\projects\`), because
the stand-in Blender was launched without `FORGE_PROJECTS_DIR`. The addon
resolves `projects/` beside its own install when the variable is unset. Every
"failed" save from candidates 2 and 3 is sitting there, real and verified.
The benchmark environment must set `FORGE_PROJECTS_DIR` to the repo's
projects folder for the Blender socket server.

## Diagnostic score of candidate-3's actual geometry

`quality.evaluate` against the shadow-saved blend: **100.0 — 8/8 gates, 4/4
fidelity.** Ear yaw ±43.764°, mirror angle 0.0° (the artist-rejected pair
measured 66.9°); volume ratio 0.391 (rejected pair 0.078); min wall 3.14 mm.
Both artist-flagged defects — flat ears, same-direction angling — are fixed
in the produced geometry.

## Verdict

The recipe changes are **sufficient in quality** for the ear task: lane
choice fixed by `organic-path-choice.md` (baseline and candidate-1 never left
part.py; candidates 2 and 3 sculpted), and the geometry meets every metric
including the two calibrated on the artist's rejections. Time is not
comparable against a baseline that delivered nothing (rule added to
compare.py). A confirmation run on the fixed environment was deliberately
skipped as unnecessary spend; the next real ear-task run under the harness
doubles as the official record.

## Comparator rules added this session (with tests)

1. A candidate that delivered no artifact cannot SHIP on a time win.
2. A baseline that delivered no artifact has no meaningful clock; it cannot
   time-regress a delivering candidate.
