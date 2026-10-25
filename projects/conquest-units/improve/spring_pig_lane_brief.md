LANE: spring_pig v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/spring_pig_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section SPRING PIG) (artist
verbatim + binding reading). SHEET: design/reference/spring_pig-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/spring_pig/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 5 framed panel(s), 70 title blob(s) and 197 rule line(s) before the split (was --region)
  - side axis 997.0 px (centre of the floor contact span); the feet-base rule says 1023.0
  - side view faces image-left (default, colour score +0.02) -> meshgen view 'left'; WEAK evidence - LOOK at the head before the proxy runs (--side-facing to set it)
  - front-vs-side height: front 6.07 u, side 6.33 u (+4.3%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.92, side length +0.7%, sha 10b34e07, seed 56); panel: design/reference/spring_pig/proxy/spring_pig_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/spring_pig_build.py + improve/spring_pig_parts.py + palettes/spring_pig/default.json (+ any
palettes/spring_pig/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: round pig biped with a coiled spring tail; light flow (proxy as base). No glow. Keep the goggle, snout and ears dense.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/spring_pig_build.py, improve/spring_pig_parts.py, palettes/spring_pig/*.json, and the runner's own
  outputs (improved/spring_pig*, improved/textures/spring_pig_*, rigged/spring_pig*, renders/spring_pig/*,
  improve/log_spring_pig_*, improved/check_spring_pig.json, rigged/check_spring_pig.json).
  The scaffolded improve/spring_pig_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/spring_pig/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/spring_pig_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/spring_pig_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/spring_pig/spring_pig_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init spring_pig   (writes the stub improve/spring_pig_build.py, improve/spring_pig_finish.json,
     palettes/spring_pig/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/spring_pig_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate spring_pig and python -P improve/proxy_finish.py panel spring_pig.
  6. Fill improve/spring_pig_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/spring_pig_finish.json and improve/spring_pig_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/spring_pig/spring_pig_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): body / head are good (mean IoU 0.92). Two interpretation items:
- The SPRING TAIL came out thinner and tangled vs the sheet's clean, fat, evenly spaced pink coil (~4 turns, from
  the lower back to the floor). The tail is not the face: you MAY replace it with a clean procedural helix (cut the
  proxy coil, add a tube helix following the sheet's side + back coil silhouette, pink 244,152,153), one opt-in
  region with a "why".
- The goggle: the sheet's front draws ONE lens (image-left eye) with the strap across; its side view shows a lens
  too, and its back view shows NO strap; the proxy wraps the strap round the back of the head. Leave the goggle as
  generated and REPORT the sheet's inconsistency (the artist decides). Face / snout / ears as generated.

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/spring_pig_finish.json, improve/spring_pig_STATE.md, renders/spring_pig/spring_pig_finish_panel.png.
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
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/spring_pig_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/spring_pig/spring_pig_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): (a) Artist: 'for spring pig the end of tail should be wider like the ref pick so it can believed like he's flying/floating with the tail' + design/reference/spring_pig-v1-tail-propeller-reference.png (a bamboo-copter toy: a slim stick ending in a small hub with TWO long flat rounded blades). Reading: the BOTTOM END of the spring tail widens into a two-blade PROPELLER like that toy - a small rounded hub at the coil's end with two flat, slightly twisted, rounded-tip blades (span ~1.6-2.0 u, ~0.35 u wide, thin), lying roughly horizontal at the floor end of the tail, in the tail's pink (or a slightly darker pink), so he reads as able to fly / hover on it. Build it as closed geometry with the EXISTING ops only (light.adds: rows of small-radius capsules, r ~0.03-0.05, make a thin flat blade; another lane is editing improve/proxy_finish_light.py right now, so do NOT edit it - if the existing ops truly cannot make a blade, report that and stop at the symmetric repose). Feet still stand on the floor: the propeller sits at the floor beside/behind the feet, not under them. Close-up front/side/threequarter. (b) Symmetric rest pose: v1 mirror p99 0.352 u (snout shifted to -x, legs staggered toe y -0.81 vs -0.34; the single-lens goggle is a design asymmetry and stays). The rebuilt coil already sits behind; keep it centred on the midline.

==== V3 (2026-10-17, artist face note; this section WINS over V2 and everything above) ====
SPEC: design/review-log.md "2026-10-17 — Spring Pig v2 face note". The proxy was RE-GENERATED symmetric
(design/reference/spring_pig/proxy/, sha cae2eed9, gate reliable 0.914; intake.json proxy block updated; renders
proxy/sources/symb_views.png). The old proxy is in proxy_v1_3q_front/ (do not use it).
Rebuild the unit on the NEW proxy, keeping from v2: the clean spring coil + the two-blade PROPELLER (light.adds in
improve/spring_pig_finish.json; re-fit the coil root to the new proxy's lower back, keep it on the midline, cut the
new proxy's own coil the same way v1 did), the tail chain to the propeller hub, the colours.
Face: the new head is symmetric - snout centred and forward, two goggle lenses on one strap, matched ears - but the
snout's FRONT FACE is plain pink: paint TWO NOSTRILS (dark ovals, the sheet's nostril colour, symmetric about x = 0,
crisp within their own outline) on it. The goggle lenses should read as dark glass with a brass ring (recolour if the
generator left them dull). Keep the face geometry as generated. Rest pose should already be near-symmetric; repose
only what is off (report mirror p99 before / after - the target is a clearly symmetric head).
Close-ups: head front, side, threequarter. Update improve/spring_pig_STATE.md to v3.

==== V3.1 (2026-10-17, artist "the snout needs to be cynlidrincal"; WINS over V3 for the snout only) ====
Replace the v3 box snout with a CYLINDER: axis straight forward (-y) on x = 0, round cross-section (diameter ~ the
sheet side view's snout height, measure it in sheet px), length from the sheet side view, a FLAT front disc with a
slightly rounded rim, the two nostrils re-painted on that disc (symmetric about x = 0, crisp). Cut the box snout
(light.cuts, one-voxel margin, stop at the face so the cheeks stay), add the cylinder as closed geometry and union it
before the remesh, pink like the face (recolour the new surface the face pink). If light.adds cannot make a capped
cylinder with a flat face, add ONE opt-in primitive to improve/proxy_finish_light.py (e.g. a "cylinder" entry in
light.adds: centre, axis, radius, half-length, rim radius); absent = no change, prove it with frost_calf's DIGEST
362f5f93d32ac747; leave the other opt-in ops (mirror_copy, belly, yaw_whole, head_graft) intact. Close-ups: head front,
side, threequarter.
