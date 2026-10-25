LANE: spike_beak_3 v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/spike_beak_3_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNIT: Spike Beak 3 (artist reference sheet, the tall one)" (artist
verbatim + binding reading). SHEET: design/reference/spike_beak_3-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/spike_beak_3/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 3 framed panel(s), 92 title blob(s) and 188 rule line(s) before the split (was --region)
  - views fused by touching parts: split at x 397,772 by a watershed along the weakest contact (the plain-column equivalent is --cuts 397,772; --assign / --cuts override)
  - front floor 782 px = the lowest body contact; ignored a part with no leg-like support hanging 15 px lower (tail curl / pool / unpaired tail) (raw lowest row 797)
  - back axis 585.0 px (mirror-symmetry of the body mass); the feet-base rule says 592.5
  - side floor 807 px = the lowest body contact; ignored a part with no leg-like support hanging 10 px lower (tail curl / pool / unpaired tail), 1 narrow end contact(s) (tail tip) (raw lowest row 817)
  - side axis 1041.5 px (centre of the floor contact span); the feet-base rule says 1007.5
  - side view faces image-left (flag, colour score -0.27) -> meshgen view 'left'
  - front-vs-side height: front 6.59 u, side 7.14 u (+8.3%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.89, side length +0.5%, sha 70178a42, seed 56); panel: design/reference/spike_beak_3/proxy/spike_beak_3_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/spike_beak_3_build.py + improve/spike_beak_3_parts.py + palettes/spike_beak_3/default.json (+ any
palettes/spike_beak_3/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: tall blue bird biped (the tall Spike Beak); light flow (proxy as base) PLUS three artist-requested deviations (see UNIT NOTE). No glow.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/spike_beak_3_build.py, improve/spike_beak_3_parts.py, palettes/spike_beak_3/*.json, and the runner's own
  outputs (improved/spike_beak_3*, improved/textures/spike_beak_3_*, rigged/spike_beak_3*, renders/spike_beak_3/*,
  improve/log_spike_beak_3_*, improved/check_spike_beak_3.json, rigged/check_spike_beak_3.json).
  The scaffolded improve/spike_beak_3_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/spike_beak_3/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/spike_beak_3_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/spike_beak_3_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/spike_beak_3/spike_beak_3_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init spike_beak_3   (writes the stub improve/spike_beak_3_build.py, improve/spike_beak_3_finish.json,
     palettes/spike_beak_3/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/spike_beak_3_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate spike_beak_3 and python -P improve/proxy_finish.py panel spike_beak_3.
  6. Fill improve/spike_beak_3_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/spike_beak_3_finish.json and improve/spike_beak_3_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/spike_beak_3/spike_beak_3_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator): proxy gate PASS (mean IoU 0.89). THE ARTIST'S THREE BINDING CHANGES (verbatim in the spec
entry) - these are explicit artist requests, so they override the light flow's "never touch the face" for exactly
these regions, each a separate opt-in config entry with its "why":
  1. The yellow beak MASK's top portion ABOVE THE EYE extends further BACK over the head (side view: today the mask
     stops just behind the eye; carry its upper edge back over the crown). Geometry where needed (a thin conforming
     shell / light.adds hugging the head) + recolour to the beak yellow (224,173,91); keep the eyes and the beak tip
     exactly as generated.
  2. The WHITE stomach feathers (210,218,236) wrap further BACK along the sides (recolour3d on the flank feathers
     behind today's white boundary; colour only).
  3. The TAIL is LONGER (extend the tail feathers behind / below, light.adds or a stretch of the tail region only,
     white-tipped like the sheet's back view).
Proxy defects to fix lightly: orange-brown blobs at the head sides / behind the mask (front + back views) that the
sheet does not draw -> recolour to the surrounding blue / mask yellow; the side-view beak droops and reads olive -
recolour to the beak yellow; reshape only if it clearly departs from the sheet's straight beak (report what you did).
Show the three changes in the panel / a close-up so the artist can judge them.

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/spike_beak_3_finish.json, improve/spike_beak_3_STATE.md, renders/spike_beak_3/spike_beak_3_finish_panel.png.
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
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/spike_beak_3_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/spike_beak_3/spike_beak_3_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): (A) the artist notes "2026-10-17 — Spike Beak 3 v1 notes" in design/review-log.md: LOOK at the three annotation images named there. (1) brow spikes: long distinct blade spikes sweeping back + up past the crest feathers, one per side (light.adds blade made of thin capsules, mask yellow; extend the v1 brow band); (2) eyes readable: enlarge / sharpen the eye paint (cream ring 230,215,170-ish from the sheet, dark navy iris, small highlight) inside a crisp outline, clear of the mask edge (eye_rim / eye_iris idiom, improve/frost_giant_child_finish.json); (3) white chest tufts longer: extend the white V feather tips down and outward past the legs (light.adds thin feather blades or light.pulls on the existing white tips, painted sheet white 210,218,236); (4) a SECOND larger TAIL section in WHITE below / behind the tail tip (back-view outline), blade feathers. Before/after close-up per item. (B) Symmetry pass too: use the v1 report.s asymmetry numbers in improve/spike_beak_3_STATE.md (turned head / swung tail / staggered legs / forward arm) and repose them; keep every v1 colour / glow / cut / add as is. If the unit has a tail, rig a tail chain that rests centred.
