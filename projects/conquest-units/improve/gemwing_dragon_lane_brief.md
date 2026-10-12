LANE: gemwing_dragon v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/gemwing_dragon_STATE.md. SPEC: design/review-log.md entry "{{DATE}} — NEW UNIT: {{TITLE}}" (artist
verbatim + binding reading). SHEET: design/reference/gemwing_dragon-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/gemwing_dragon/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 4 framed panel(s), 122 title blob(s) and 198 rule line(s) before the split (was --region)
  - views fused by touching parts: split at x 685 by a watershed along the weakest contact (the plain-column equivalent is --cuts 343,685; --assign / --cuts override)
  - back floor 718 px = the lowest body contact; ignored a part with no leg-like support hanging 129 px lower (tail curl / pool / unpaired tail), 1 narrow end contact(s) (tail tip) (raw lowest row 847)
  - back axis 518.0 px (mirror-symmetry of the body mass); the feet-base rule says 572.0
  - side floor 714 px = the lowest body contact; ignored a part with no leg-like support hanging 108 px lower (tail curl / pool / unpaired tail), 8 narrow end contact(s) (tail tip) (raw lowest row 822)
  - side axis 798.5 px (centre of the floor contact span); the feet-base rule says 1007.5
  - front and back body widths disagree (0.82, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - side view faces image-left (auto, colour score -0.07) -> meshgen view 'left'
  - front-vs-side height: front 6.19 u, side 6.45 u (+4.2%) - over 3 %: reconcile them and say how
  - no flat palette chips: 6 gradient-shaded chips found with a relaxed search (palette_fallback)
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by -9.1%) (mean IoU 0.81, side length -9.1%, sha 78bb91e1, seed 56); panel: design/reference/gemwing_dragon/proxy/gemwing_dragon_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/gemwing_dragon_build.py + improve/gemwing_dragon_parts.py + palettes/gemwing_dragon/default.json (+ any
palettes/gemwing_dragon/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: {{DELTA}}

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/gemwing_dragon_build.py, improve/gemwing_dragon_parts.py, palettes/gemwing_dragon/*.json, and the runner's own
  outputs (improved/gemwing_dragon*, improved/textures/gemwing_dragon_*, rigged/gemwing_dragon*, renders/gemwing_dragon/*,
  improve/log_gemwing_dragon_*, improved/check_gemwing_dragon.json, rigged/check_gemwing_dragon.json).
  The scaffolded improve/gemwing_dragon_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/gemwing_dragon/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/gemwing_dragon_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/gemwing_dragon_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/gemwing_dragon/gemwing_dragon_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
