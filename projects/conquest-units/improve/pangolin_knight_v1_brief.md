LANE: pangolin_knight v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/pangolin_knight_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNITS x2: Pangolin Knight, Shadow Mole (artist sheets)" (artist
verbatim + binding reading). SHEET: design/reference/pangolin_knight-character-sheet.webp — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/pangolin_knight/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/pangolin_knight_build.py + improve/pangolin_knight_parts.py + palettes/pangolin_knight/default.json (+ any
palettes/pangolin_knight/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - Upright BIPED pangolin: SDF body (cream face / chest / belly, a soft fur edge), short snout + pink nose, simple
    expressive black eye (closed decal).
  - SCALES are the identity: overlapping rounded-pointed leaf scales in rows over the head (a HELMET + crest),
    back, shoulders, flanks and legs; "simple layered scales, smooth rounded shapes, NOT TOO MANY scales". Closed
    thin plate parts (or SDF plates) with a slight lift at each tip, layered so each row overlaps the next; 2-3
    brown tones by row / scale (palette chips).
  - A LONG SEGMENTED TAPERING TAIL of scaled bands curving down to the floor behind (vine chain, 4 bones).
  - Forelimbs held forward: armour-band GAUNTLETS + dark claws (3); short legs, clawed dark feet.
  - Unarmed (question P1). Rig: root > hips > spine > chest > head; chest > arm.{L,R}.0,1,2; hips > leg.{L,R}.0,1,2;
    hips > tail.0..3. Clips: idle = breathing + head bob + tail sway (feet planted); walk = in-place waddle-walk.
  - The sheet FRONT is 3/4: measure from the true orthos BACK + SIDE (intake ortho_frame.views); front = 3/4 check.
  - Tier REGULAR [8000, 22000]; default skin only; clips PLACEHOLDER (say so in conquest_clip_status); no glow.
  - Colour edges iso-cut; BUILD_CONTRACT 'Known kit hazards' apply (BAND >= max k; soft-form language: no rows of
    sharp cones; AO floor per region class so nothing bakes to black).


FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/pangolin_knight_build.py, improve/pangolin_knight_parts.py, palettes/pangolin_knight/*.json, and the runner's own
  outputs (improved/pangolin_knight*, improved/textures/pangolin_knight_*, rigged/pangolin_knight*, renders/pangolin_knight/*,
  improve/log_pangolin_knight_*, improved/check_pangolin_knight.json, rigged/check_pangolin_knight.json).
  The scaffolded improve/pangolin_knight_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/pangolin_knight_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/pangolin_knight_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/pangolin_knight/pangolin_knight_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
