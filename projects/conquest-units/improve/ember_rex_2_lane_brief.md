LANE: ember_rex_2 v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/ember_rex_2_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section EMBER REX 2) (artist
verbatim + binding reading). SHEET: design/reference/ember_rex_2-character-sheet-split.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/ember_rex_2/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - front axis 156.5 px (mirror-symmetry of the body mass); the feet-base rule says 183.0
  - side axis 987.5 px (centre of the floor contact span); the feet-base rule says 970.5
  - front and back body widths disagree (0.82, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - probable 3/4 / perspective view(s): front (mirror IoU 0.60, tone asymmetry 0.56); back (mirror IoU 0.64, tone asymmetry 0.53) -> ortho_views back,side - BOTH look 3/4, the overlay is indicative only (a 3/4 SIDE is not auto-detected: check it)
  - side view faces image-left (flag, colour score -0.10) -> meshgen view 'left'
  - front-vs-side height: front 6.69 u, side 7.21 u (+7.8%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by +5.2%) (mean IoU 0.82, side length +5.2%, sha 5f069737, seed 56); panel: design/reference/ember_rex_2/proxy/ember_rex_2_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/ember_rex_2_build.py + improve/ember_rex_2_parts.py + palettes/ember_rex_2/default.json (+ any
palettes/ember_rex_2/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: tall living-flame phoenix biped (sheet 10); light flow (proxy as base). Glow: the flame plumage and the leg cracks are drawn self-lit (see UNIT NOTE). Keep the crest, beak and eye dense.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/ember_rex_2_build.py, improve/ember_rex_2_parts.py, palettes/ember_rex_2/*.json, and the runner's own
  outputs (improved/ember_rex_2*, improved/textures/ember_rex_2_*, rigged/ember_rex_2*, renders/ember_rex_2/*,
  improve/log_ember_rex_2_*, improved/check_ember_rex_2.json, rigged/check_ember_rex_2.json).
  The scaffolded improve/ember_rex_2_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/ember_rex_2/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/ember_rex_2_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/ember_rex_2_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/ember_rex_2/ember_rex_2_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.

==== LIGHT FLOW OVERRIDE (2026-10-16 batch; this section WINS over the procedural DELIVERABLE / REFERENCE IMPLEMENTATION above) ====
The artist prefers the Pixal3D proxy over hand builds ("all of the work it did looks better than what we did"), and a
heavy finish broke a face before (gemwing: "in trying to fix the symmetry and make changes we broke some things, like
the mouth"). This unit ships on the LIGHT finish, exactly like the shipped units sand_drill_worm and vulture_creature
(read improve/vulture_creature_finish.json, improve/vulture_creature_STATE.md and docs/conquest-new-unit-flow.md
sections 5 + 5b first; copy their idioms):
  1. python -P improve/proxy_finish.py init ember_rex_2   (writes the stub improve/ember_rex_2_build.py, improve/ember_rex_2_finish.json,
     palettes/ember_rex_2/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
  2. Tune ONLY what the light flow needs: `refloor` (floor at the lowest FOOT contact when sheet_proxy floored a tail /
     hem / feather tip; then rescale to the sheet front height), `light.dense` boxes so the HEAD / FACE keeps its
     detail, and glow ONLY where the sheet draws it (light.glow3d + palette emission regions, as vulture_creature's
     eyes). Optional light.* fixes (recolour3d, tint_clamp, floor_lift, despeckle, erase3d) only for a visible defect
     against the sheet, each with a "why".
  3. NEVER on the light flow: symmetrise, repose, straighten, part replacement or cuts on the head / face. The
     symmetric-rest-pose rule yields here: if the proxy is visibly asymmetric (a swung tail, a turned head), REPORT it
     with numbers (mirror p99) and leave it - the artist picks.
  4. If the proxy is visibly wrong (a melted / missing part the sheet clearly draws, a fused limb), report it with
     the panel and finish the run anyway. Do NOT regenerate the proxy yourself (the GPU queue and design/reference/
     belong to the orchestrator); say which views a forced 3-view retry should use and the orchestrator reruns it.
  5. Full run: powershell -NoProfile -File improve/ember_rex_2_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate ember_rex_2 and python -P improve/proxy_finish.py panel ember_rex_2.
  6. Fill improve/ember_rex_2_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/ember_rex_2_finish.json and improve/ember_rex_2_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/ember_rex_2/ember_rex_2_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): proxy generated from front + side (the front view, flagged 3/4, was
accepted for the proxy by the orchestrator: design/reference/ember_rex_2/intake_proxy.json; intake.json's view_checks
unchanged). Gate failed on side length only (+5.2 %; mean IoU 0.82): use `fit_side_length`. The intake ran on a
split-helper copy of the sheet (the back view's tail tip under the side view blacked out): a small stray scrap at the
side view's lower-left edge is a split remnant, not part of the character.
Items: (a) the flame crest and a few back flames came out as FLAT PAPER PANELS (like the moose antlers did); leave
the head / crest as generated, but loose panels clearly standing off the body (shoulders / back, not the head) may be
cut, with a "why"; (b) TONE: the proxy reads dark maroon / brown where the sheet is bright red-orange with yellow
flame tips (225,76,58 / 253,124,67 / 253,201,100) and a cream neck (246,214,182): check the texture, tint only;
(c) GLOW: the sheet draws this bird as living flame: a modest emissive on the flame-tip faces (the bright orange /
yellow tones) and the leg cracks via light.glow3d; keep the dark-red body unlit so the form still reads. Report the
glow face share.

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/ember_rex_2_finish.json, improve/ember_rex_2_STATE.md, renders/ember_rex_2/ember_rex_2_finish_panel.png.
1. SYMMETRIC REST POSE (all units, artist: "our characters should be symmetrical head turns are done through posing so
   our models remain flexible"). The proxy copied the sheet's 3/4 pose (turned head, swung tail, staggered legs, a
   forward arm). Undo it with BONES via the finish config's `repose` block (improve/proxy_finish_repose.py; working
   example: improve/crystal_canine_finish.json "repose"):
   - legs / arms: {"symmetric": ["leg"(, "arm")], "match": "mean", "plant": true} - mirror-symmetric pairs, feet planted;
   - head / neck: {"bones": ["neck","head"], "heading": "-y", "distribute": true} - the face straight ahead;
   - tail: centred on the midline ({"chain": "tail", "heading": "+y"} or "to_points" on the sheet's SIDE-view curve at
     x = 0), with a tail CHAIN in the rig so it can be posed to either side later;
   - a `rigid` box on the head/face bone so the face moves as one rigid body (never deformed or re-sculpted).
   NEVER mirror-average or symmetrise geometry. Report mirror p99 before -> after, edge stretch p99, self-intersections,
   the rigid-face check, and feet-on-floor. A design asymmetry the sheet draws on purpose stays.
   If repose cannot express something (e.g. the kit has no target for it), report the gap with the numbers - do not
   patch the kit unless this section says so.
2. UNIT CHANGES: see the UNIT V2 NOTE below.
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/ember_rex_2_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/ember_rex_2/ember_rex_2_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): Symmetry pass only (artist 2026-10-17: rest pose symmetric, turns are posing). Use the v1 report's asymmetry numbers in improve/ember_rex_2_STATE.md (turned head / swung tail / staggered legs / forward arm) and repose them; keep every v1 colour / glow / cut / add as is. If the unit has a tail, rig a tail chain that rests centred.
UNIT V2 NOTE 2 (orchestrator, artist 2026-10-17 "Ember Rex 2 v1 notes" in design/review-log.md - LOOK at the two
annotation images): (1) EYES: v1 has no readable eye. Give each side of the head a crisp eye at the sheet's position
(intense yellow-orange iris ~255,190,60 with a dark pupil and a thin dark rim; eye_rim / eye_iris paint idiom from
improve/frost_giant_child_finish.json; mirrored after the symmetric repose); optional small eye glow. (2) FIRE CONTRAST:
every fire part (flame collar band across neck/shoulders - outlined in the image -, flame crest, flame-tipped wing /
tail / body feathers, chest flame) must contrast strongly with the body: push the fire faces brighter + more saturated
(orange 253,124,67 -> yellow 253,201,100 -> pale-yellow hot cores toward the tips) and raise their glow (emission_scale
up from v1's 0.35; flame_tips can go ~0.8-1.0 with hot cores brighter), while the BODY (non-fire faces) goes darker /
more muted red (toward 68,38,42 / 120,35,40) and stays unlit. Select fire vs body by colour (L*, hue) and by region
(collar band, crest, feather tips), not by a broad sphere. Before/after close-ups: head (eye + crest), collar, a wing.

==== V2b (2026-10-17, orchestrator; WINS over the V2 section's repose instructions) ====
The proxy was RE-GENERATED from symmetric views (design/reference/ember_rex_2/proxy/, sha c36d9494; renders
proxy/sources/sym_views.png): head straight, crest centred, two legs, tail centred behind. The old proxy is in
proxy_v1_3q/ (do not use it). Rebuild on the NEW proxy (mean IoU 0.69 - expected, see the proxy note; no forced
rescale to the 3/4 front height). It should need little or no repose: measure head + body mirror p99 first.
Then do UNIT V2 NOTE 2 in full (crisp yellow-orange eyes on both sides; fire parts much brighter / more saturated with
stronger glow, the body darker muted red and unlit). The v1 config's cuts / refloor / recolour were for the old proxy:
redo what is still needed on the new one. Rig a centred tail chain. Close-ups: head front + side (eyes), collar, a wing,
tail.
