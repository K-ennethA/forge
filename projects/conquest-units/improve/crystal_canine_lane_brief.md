LANE: crystal_canine v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/crystal_canine_STATE.md. SPEC: design/review-log.md entry "{{DATE}} — NEW UNIT: {{TITLE}}" (artist
verbatim + binding reading). SHEET: design/reference/crystal_canine-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/crystal_canine/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 0 framed panel(s), 157 title blob(s) and 309 rule line(s) before the split (was --region)
  - dropped 1 non-view segment(s) (detail panels / labels: short, or standing above the views' floor band): x 1218-1425
  - views fused by touching parts: split at x 422 by a watershed along the weakest contact (the plain-column equivalent is --cuts 422,760; --assign / --cuts override)
  - back floor 785 px = the lowest body contact; ignored a part with no leg-like support hanging 1 px lower (tail curl / pool / unpaired tail) (raw lowest row 786)
  - front and back body widths disagree (0.76, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - side view faces image-left (auto, colour score -0.06) -> meshgen view 'left'
  - front-vs-side height: front 6.23 u, side 6.21 u (-0.3%)
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by +5.9%) (mean IoU 0.76, side length +5.9%, sha ab082a63, seed 56); panel: design/reference/crystal_canine/proxy/crystal_canine_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/crystal_canine_build.py + improve/crystal_canine_parts.py + palettes/crystal_canine/default.json (+ any
palettes/crystal_canine/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: {{DELTA}}

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/crystal_canine_build.py, improve/crystal_canine_parts.py, palettes/crystal_canine/*.json, and the runner's own
  outputs (improved/crystal_canine*, improved/textures/crystal_canine_*, rigged/crystal_canine*, renders/crystal_canine/*,
  improve/log_crystal_canine_*, improved/check_crystal_canine.json, rigged/check_crystal_canine.json).
  The scaffolded improve/crystal_canine_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/crystal_canine/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/crystal_canine_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/crystal_canine_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/crystal_canine/crystal_canine_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
