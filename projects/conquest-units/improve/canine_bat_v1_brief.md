LANE: canine_bat v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/canine_bat_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)" (artist
verbatim + binding reading). SHEET: design/reference/canine_bat-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/canine_bat/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/canine_bat_build.py + improve/canine_bat_parts.py + palettes/canine_bat/default.json (+ any
palettes/canine_bat/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - The sheet FRONT is 3/4: measure from the true orthos BACK + SIDE (intake ortho_frame.views); front = 3/4 check.
  - A QUADRUPED (the first four-legged forge creature): body along -Y (head at the front), four short sturdy legs
    with rounded paws (toe clumps), a small tail. SDF shell: body, chest, head, snout, legs, paws, tail smooth-
    unioned; soft short fur read (fringed silhouette at the ruff / belly / ear bases, blunt soft tufts; the
    shadowbun v1 gate lesson: no rows of sharp cones).
  - OVERSIZED upright pointed EARS: thin membrane (closed thin solids, a mauve inner region + dark rim, faint vein
    strips), soft fur at the base. Big round dark EYES with a pale rim + highlight (closed decals), a pig/bat SNOUT
    (mauve-grey, two nostrils), two small FANGS (closed cones). A fluffy pale NECK RUFF (scalloped soft fringe).
    Lighter fur on the lower legs / paws and inner thighs (iso-cut regions). No wings (artist question C1 open).
  - Rig: root > hips > spine > chest > neck > head > ear.{L,R}.0,1 + jaw-free head; hips > leg.{FL,FR,BL,BR}.0,1,2
    (2 = paw, never scaled); hips > tail.0,1. Clips: idle = breathing + ear twitch + tail wag (paws planted),
    walk = in-place 4-beat quadruped trot (diagonal pairs, paws never below z 0, root still).
  - Tier REGULAR [8000, 22000]; default skin only; clips PLACEHOLDER (say so in conquest_clip_status); no glow.
  - Colour edges iso-cut; BUILD_CONTRACT 'Known kit hazards' apply (BAND >= max k; fur language).


FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/canine_bat_build.py, improve/canine_bat_parts.py, palettes/canine_bat/*.json, and the runner's own
  outputs (improved/canine_bat*, improved/textures/canine_bat_*, rigged/canine_bat*, renders/canine_bat/*,
  improve/log_canine_bat_*, improved/check_canine_bat.json, rigged/check_canine_bat.json).
  The scaffolded improve/canine_bat_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/canine_bat_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/canine_bat_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/canine_bat/canine_bat_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
