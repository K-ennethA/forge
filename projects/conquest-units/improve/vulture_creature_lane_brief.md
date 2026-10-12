LANE: vulture_creature v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged) ; docs/conquest-new-unit-flow.md
(smart intake -> sheet proxy -> build -> numeric gate).
HOUSE RULES: the SHEET IS THE SPEC (build only what it draws; report every deviation, never guess) ; the rest
pose is SYMMETRIC (a swung tail / staggered legs on the sheet are built mirror-symmetric and reported) ; movement
intent and in-game height are the only artist questions (clips ship as PLACEHOLDER until answered).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms EXCEPT the bake reference (firefly bakes against smooth; you bake against FLAT, BUILD_CONTRACT step 5);
shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/vulture_creature_STATE.md. SPEC: design/review-log.md entry "{{DATE}} — NEW UNIT: {{TITLE}}" (artist
verbatim + binding reading). SHEET: design/reference/vulture_creature-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/vulture_creature/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - dark subject on a dark sheet at x 32-400; 400-765; 765-1186 (the threshold lost 44%, 57%, 49% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (16701, 21673, 19732 px)
  - front floor 809 px = the lowest body contact; ignored a part with no leg-like support hanging 1 px lower (tail curl / pool / unpaired tail) (raw lowest row 810)
  - back floor 831 px = the lowest body contact; ignored a part with no leg-like support hanging 9 px lower (tail curl / pool / unpaired tail) (raw lowest row 840)
  - side floor 811 px = the lowest body contact; ignored a part with no leg-like support hanging 17 px lower (tail curl / pool / unpaired tail), 2 narrow end contact(s) (tail tip) (raw lowest row 828)
  - side axis 1051.5 px (centre of the floor contact span); the feet-base rule says 1025.5
  - side view faces image-left (flag, colour score -0.21) -> meshgen view 'left'
  - front-vs-side height: front 6.84 u, side 6.92 u (+1.2%)
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.77, side length +0.8%, sha feabf14e, seed 56); panel: design/reference/vulture_creature/proxy/vulture_creature_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/vulture_creature_build.py + improve/vulture_creature_parts.py + palettes/vulture_creature/default.json (+ any
palettes/vulture_creature/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: {{DELTA}}

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/vulture_creature_build.py, improve/vulture_creature_parts.py, palettes/vulture_creature/*.json, and the runner's own
  outputs (improved/vulture_creature*, improved/textures/vulture_creature_*, rigged/vulture_creature*, renders/vulture_creature/*,
  improve/log_vulture_creature_*, improved/check_vulture_creature.json, rigged/check_vulture_creature.json).
  The scaffolded improve/vulture_creature_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/vulture_creature/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/vulture_creature_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/vulture_creature_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/vulture_creature/vulture_creature_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
