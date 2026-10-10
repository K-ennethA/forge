LANE: raven_rogue v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/raven_rogue_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)" (artist
verbatim + binding reading). SHEET: design/reference/raven_rogue-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/raven_rogue/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/raven_rogue_build.py + improve/raven_rogue_parts.py + palettes/raven_rogue/default.json (+ any
palettes/raven_rogue/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - True orthos FRONT + SIDE (back is a true ortho too: intake views were split with --region/--assign; read the
    overlay).
  - A chunky UPRIGHT RAVEN (~5.3 u): SDF egg-ish body + round head (one mass, a slight neck), a cream chest BIB
    (iso-cut, soft fringed edge into the dark plumage), swept head CREST (a few soft blunt feather clumps).
  - A LARGE curved dark-grey BEAK (closed part, matte), heavy-lidded intense pale-gold EYES (closed decals with a
    lid line, a slight frown), a red slash SCAR across one brow (decal), a small FANG at the beak corner (closed cone).
  - LAYERED CAPE-like WINGS draped round the body from the shoulders, with torn / ragged tips grading to violet:
    closed thin feather-clump solids (or double-sided sheets per the firefly smoke precedent, reported) on vine
    chains; NOT rows of sharp cones (fur / feather language: soft, layered, ragged).
  - Short legs with simple leg BANDS, clawed grey FEET (3 front toes + claws), a short ragged TAIL.
  - Unarmed (artist question R1 open: no weapon unless the artist says so).
  - Rig: root > hips > body > head + beak-free head; body > wing.{L,R}.0,1,2 (cape chains); hips > leg.{L,R}.0,1,2
    (2 = foot, planted); hips > tail.0,1. Clips: idle = cape ruffle + head tilt + breathing (feet planted); walk =
    in-place hop-strut (bird gait, feet never below z 0, root still).
  - Tier REGULAR [8000, 22000]; default skin only; clips PLACEHOLDER (say so in conquest_clip_status); no glow.
  - Colour edges iso-cut; BUILD_CONTRACT 'Known kit hazards' apply (BAND >= max k; fur / feather language).


FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/raven_rogue_build.py, improve/raven_rogue_parts.py, palettes/raven_rogue/*.json, and the runner's own
  outputs (improved/raven_rogue*, improved/textures/raven_rogue_*, rigged/raven_rogue*, renders/raven_rogue/*,
  improve/log_raven_rogue_*, improved/check_raven_rogue.json, rigged/check_raven_rogue.json).
  The scaffolded improve/raven_rogue_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/raven_rogue_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/raven_rogue_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/raven_rogue/raven_rogue_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
