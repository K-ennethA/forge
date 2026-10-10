LANE: shadowbun v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/shadowbun_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNIT: Shadowbun (artist reference sheet) + Seedant answers + size lineup" (artist
verbatim + binding reading). SHEET: design/reference/shadowbun-character-sheet.webp — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/shadowbun/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/shadowbun_build.py + improve/shadowbun_parts.py + palettes/shadowbun/default.json (+ any
palettes/shadowbun/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - THE SHEET'S FRONT IS THREE-QUARTER, not orthographic: measure from the true orthos BACK + SIDE (intake
    ortho_frame.views = back, side; the compose compares those two). Use the 3/4 front only as a check render.
    The side view faces image-left = the unit's front (-Y). The side axis in intake.json is the FEET centre.
  - SDF body shell (smooth unions): one tall EGG/PEAR body, no neck (head = the narrow top), a rounded muzzle
    proud at the front-top; short thick arms hanging close at the sides; stubby legs + big rounded feet with toe
    clumps; FUR = a fringed/tufted silhouette (a directional tuft field or clump cones smooth-unioned, tufts
    pointing down/out along the fur flow) — the bake from the SDF high carries fur strokes into the normal map.
    EARS: two upright leaf-shaped ears with jagged fur edges (closed SDF or parts; accent lavender). TAIL: a large
    cloud-puff tail low on the back built from rounded clumps (lighter lavender), clearing the floor per the side
    view.
  - Regions (iso-cut, clean lines): fur / fur_shade (optional) / ear / belly / belly_line (the thin pale line
    along the SCALLOPED top edge of the round belly patch — scallop the field, cut the line as its own band) /
    tail / mouth / eye. Face as closed conforming decals (eye = white round dot, two lateral eyes one per side of
    the head — artist question B2 open; mouth = a row of soft rounded white teeth-bumps along the muzzle's
    front-lower edge, "cloud-like").
  - No emission (all-zero Glow, question B3), opaque body material only: drop the smoke/flame materials and the
    glow_tiers gate; default skin only (artist: alternate colours later).
  - Rig: root > hips > body > head (upper egg) > ear.{L,R}.0,1 ; body > arm.{L,R}.0,1 ; hips > leg.{L,R}.0,1 (foot)
    ; hips > tail.0,1. <= 4 influences, smooth blends across the egg (no seams at the head/body band).
  - Clips (PLACEHOLDER, B1 open): idle 4 s (breathing, ear twitch on de-synced chains, tail fluff, small arm sway,
    feet planted); walk 2 s in-place BUNNY HOP on the stubby legs (squash on land, ears + tail follow-through,
    nothing below z 0, root still). PLACEHOLDER in conquest_clip_status.
  - Facing landmark: head centre -> the muzzle tip (midline). Focus close-ups matching the four detail panels:
    face (side-on), ear, tail, belly.
  - Tier REGULAR [8000, 22000]; fur tufts are where the budget goes — report the per-part table.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/shadowbun_build.py, improve/shadowbun_parts.py, palettes/shadowbun/*.json, and the runner's own
  outputs (improved/shadowbun*, improved/textures/shadowbun_*, rigged/shadowbun*, renders/shadowbun/*,
  improve/log_shadowbun_*, improved/check_shadowbun.json, rigged/check_shadowbun.json).
  The scaffolded improve/shadowbun_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/shadowbun_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/shadowbun/shadowbun_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
