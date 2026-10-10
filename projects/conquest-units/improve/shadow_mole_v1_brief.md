LANE: shadow_mole v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/shadow_mole_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNITS x2: Pangolin Knight, Shadow Mole (artist sheets)" (artist
verbatim + binding reading). SHEET: design/reference/shadow_mole-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/shadow_mole/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/shadow_mole_build.py + improve/shadow_mole_parts.py + palettes/shadow_mole/default.json (+ any
palettes/shadow_mole/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - A near-black MOLE emerging from a SHADOW BURROW: the burrow = a soft cloud-edged near-black disc ON the floor
    (scalloped puffy rim, low, closed solid; rest pose: burrow bottom at z 0), the mole's smooth rounded dome body
    + head rising out of it (SDF), a small simple white eye with a dark pupil (closed decal).
  - PINK BURROW-STALKS rising from the burrow: one tall + two short (per the views), long flexible tubes ending in a
    FLOWER-LIKE PETAL TIP (5-7 pointed petals), gradient bright pink -> pale pink along each stalk (regions by arc
    length); a pink PETAL CROWN tuft on the head.
  - Two bright pink DIGGING CLAWS resting on the burrow rim: rounded paws with three pointed toes.
  - Rig: root > burrow (static) ; root > body > head ; body > claw.{L,R} ; root > stalk.{tall,L,R}.0,1,2 (vine
    chains) ; head > crown. Clips: idle = stalks swaying on de-synced waves + body breathing + head turn; walk =
    in-place burrow-slide (body bobs / leans, stalks trail, the burrow stays on the floor).
  - The sheet FRONT is 3/4: measure from the true orthos BACK + SIDE (intake ortho_frame.views); front = 3/4 check.
  - Tier REGULAR [8000, 22000]; default skin only; clips PLACEHOLDER (say so in conquest_clip_status); no glow.
  - Colour edges iso-cut; BUILD_CONTRACT 'Known kit hazards' apply (BAND >= max k; soft-form language: no rows of
    sharp cones; AO floor per region class so nothing bakes to black).


FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/shadow_mole_build.py, improve/shadow_mole_parts.py, palettes/shadow_mole/*.json, and the runner's own
  outputs (improved/shadow_mole*, improved/textures/shadow_mole_*, rigged/shadow_mole*, renders/shadow_mole/*,
  improve/log_shadow_mole_*, improved/check_shadow_mole.json, rigged/check_shadow_mole.json).
  The scaffolded improve/shadow_mole_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

SCRATCH: every temp file (preview blends, logs, test renders) goes under <session scratchpad>/shadow_mole_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/shadow_mole_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/shadow_mole/shadow_mole_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
