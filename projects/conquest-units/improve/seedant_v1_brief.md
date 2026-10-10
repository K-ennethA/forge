LANE: seedant v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

LAW (read, cite, don't re-derive): docs/lane-conventions.md (draft speed: ONE pass, render, report) ;
projects/conquest-units/design/character-style-guide.md (shared creature principles: palette regions,
determinism, probes) ; projects/conquest-units/improve/templates/new_unit/BUILD_CONTRACT.md (THE contract
your build honours so the scaffolded runner/render/compose work unchanged).
REFERENCE IMPLEMENTATION: improve/firefly_build.py + improve/firefly_parts.py — copy their structure, helpers
and idioms; shared kits magmoo_sdf.py / palettes.py / rigkit.py / export_glb.py are READ-ONLY.
STATE: improve/seedant_STATE.md. SPEC: design/review-log.md entry "2026-10-09 — NEW UNIT: Seedant (artist reference sheet) — first run of the kickoff process" (artist
verbatim + binding reading). SHEET: design/reference/seedant-character-sheet.webp — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/seedant/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.

DELIVERABLE: improve/seedant_build.py + improve/seedant_parts.py + palettes/seedant/default.json (+ any
palettes/seedant/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: 
  - SDF body shell: ONE smooth green gumdrop body (head+body one piece) sunk into the pot mouth, smooth-unioned
    with the two LEAF ARMS (plump flattened lobes: chain of oriented ellipsoids / round cones along a path that
    leaves the shoulder, rides OVER the pot rim lip and hangs down the outer wall, clearing the rim by its own
    thickness — arms rest ON the rim, never through it; measure the clearance and report it).
  - Closed parametric parts: the POT (lathe: tapered wall, thick rolled rim band, rim top lip with visible
    thickness, inner wall, a dark soil disc the body sinks into; regions pot / pot_rim / pot_lip (worn, lighter) /
    pot_inner / soil); the two RED LEAVES (lens/diamond cross-section blade, slightly folded on the midrib, tapered,
    splayed in a V and leaning back per the views; midrib + side-vein regions as clean face strips, e.g. a grid
    sheared along the vein direction); the SPROUT CURL (tapering tube stem -> fiddlehead spiral); the two ARM-TIP
    CURLS (darker-green spiral tubes seated on each arm tip's outer surface); FACE as closed conforming decals
    projected onto the body SDF: glossy black oval eyes (slight dome) + white highlight dots + the open smile
    (dark) with a pink tongue — clean outlines (32+ segments), not iso-cut polygons.
  - Regions: body / body_shade (optional pale-green highlight chip 201,222,148 on the upper head if it reads) /
    arm / curl / sprout / leaf / leaf_vein / leaf_midrib / eye / eye_glint / mouth / tongue / pot regions above.
    No emission anywhere (all-zero Glow); no translucency (opaque body material only — drop the smoke/flame
    material machinery). Drop firefly's glow_tiers gate; keep the repaint/skin proof only if a skin exists
    (none requested: default skin only).
  - Rig: root > pot (pot + soil, 100%) > body (lower body) > head (upper body, face, eyes) ; head > sprout.0,1 ;
    head > leaf.{L,R}.0,1,2 (vine weights) ; body > arm.{L,R}.0,1,2 (vine weights from the shoulder; the curl rides
    the arm tip bone) ; head > eye.{L,R} (eye + glint decals 100%, scaled in Z for the blink).
  - Clips (PLACEHOLDER, artist question S1 open): idle 4 s (breathing sway of body/head, red leaves rustling on
    de-synced chains, arm tips tapping the pot wall without entering it, one blink via eye.{L,R} Z-scale);
    walk 2 s in-place POT HOP (pot bone lifts with squash on land/stretch at take-off, leaves + arms follow-through;
    root never moves; nothing below z 0). Say PLACEHOLDER in conquest_clip_status.
  - Facing landmark: head centre -> the mouth centre (midline). Focus close-ups: face, leaf, arm, pot (the four
    detail panels) with views chosen to match the panels.
  - Tier REGULAR [8000, 22000]; expect the low end (report the per-part table).

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/seedant_build.py, improve/seedant_parts.py, palettes/seedant/*.json, and the runner's own
  outputs (improved/seedant*, improved/textures/seedant_*, rigged/seedant*, renders/seedant/*,
  improve/log_seedant_*, improved/check_seedant.json, rigged/check_seedant.json).
  The scaffolded improve/seedant_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it.

LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/seedant_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; your own read of
renders/seedant/seedant_sheet_vs_model.png (where the outline departs from the sheet, by view, and why);
which renders exist; the TUNABLE knobs an artist is most likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + report. Blocked = a kit/tool defect
outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report the split).
Never commit, never spawn agents, never launch anything windowed, never touch port 9876.
