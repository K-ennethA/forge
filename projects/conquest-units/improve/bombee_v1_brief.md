LANE: bombee v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/bombee_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)" (artist
verbatim + binding reading). SHEET: design/reference/bombee-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/bombee/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/bombee_build.py + improve/bombee_parts.py + palettes/bombee/default.json (+ any
palettes/bombee/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - The sheet FRONT is 3/4: measure from the true orthos BACK + SIDE (intake ortho_frame.views); front = 3/4 check.
  - SDF body: near-spherical fuzzy ball; regions iso-cut: cream face disc on the front, black / yellow bands wrapping
    behind it (back view: concentric bands round the stinger).
  - Closed parts: conical cream-tan STINGER at the rear; four small conical spikes (feet) on the underside; two
    large upper wings + two small lower wings = leaf/petal sheets, slightly translucent cream with a tan diamond
    vein lattice (firefly smoke alpha path: alphaMode BLEND double-sided); eyes (round, shiny dark brown +
    highlight), smile line, pink blush decals.
  - Rig: root > body (hover) > wing.{L,R}.{up,low} + stinger; clips: idle = hover bob + wing buzz (fast wing
    oscillation, integer harmonics), walk = in-place hover-drift (lean). Rest pose on the floor (spikes at z 0).
  - Tier REGULAR [8000, 22000]; default skin only; idle + walk PLACEHOLDER clips (say so in conquest_clip_status).
  - Face features as closed conforming decals (clean outlines); colour edges iso-cut; soft forms read SOFT
    (BUILD_CONTRACT 'Known kit hazards': fur / tuft language, BAND >= max k).


FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/bombee_build.py, improve/bombee_parts.py, palettes/bombee/*.json, and the runner's own
  outputs (improved/bombee*, improved/textures/bombee_*, rigged/bombee*, renders/bombee/*,
  improve/log_bombee_*, improved/check_bombee.json, rigged/check_bombee.json).
  The scaffolded improve/bombee_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/bombee_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/bombee_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/bombee/bombee_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
