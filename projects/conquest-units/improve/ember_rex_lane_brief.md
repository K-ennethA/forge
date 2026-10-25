LANE: ember_rex v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/ember_rex_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section EMBER REX) (artist
verbatim + binding reading). SHEET: design/reference/ember_rex-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/ember_rex/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 3 framed panel(s), 111 title blob(s) and 182 rule line(s) before the split (was --region)
  - views fused by touching parts: split at x 399,761 by a watershed along the weakest contact (the plain-column equivalent is --cuts 399,761; --assign / --cuts override)
  - front axis 219.5 px (mirror-symmetry of the body mass); the feet-base rule says 290.5
  - back floor 771 px = the lowest body contact; ignored a drop shadow (146 px), a part with no leg-like support hanging 36 px lower (tail curl / pool / unpaired tail), 1 narrow end contact(s) (tail tip) (raw lowest row 807)
  - back axis 555.0 px (mirror-symmetry of the body mass); the feet-base rule says 597.5
  - side floor 774 px = the lowest body contact; ignored a drop shadow (379 px), a part with no leg-like support hanging 39 px lower (tail curl / pool / unpaired tail) (raw lowest row 813)
  - side axis 1016.0 px (centre of the floor contact span); the feet-base rule says 1048.5
  - probable 3/4 / perspective view(s): front (mirror IoU 0.41, tone asymmetry 0.49) -> ortho_views back,side (a 3/4 SIDE is not auto-detected: check it)
  - side view faces image-left (default, colour score +0.09) -> meshgen view 'left'; WEAK evidence - LOOK at the head before the proxy runs (--side-facing to set it)
  - front-vs-side height: front 6.72 u, side 6.33 u (-5.8%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by +8.9%) (mean IoU 0.84, side length +8.9%, sha cbbd25d9, seed 56); panel: design/reference/ember_rex/proxy/ember_rex_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/ember_rex_build.py + improve/ember_rex_parts.py + palettes/ember_rex/default.json (+ any
palettes/ember_rex/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: tall phoenix raptor biped (sheet 9, the darker ember-tipped one); light flow (proxy as base). Glow ONLY on the small ember gem on the chest (the sheet draws it lit). Keep the crest, beak and eye dense.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/ember_rex_build.py, improve/ember_rex_parts.py, palettes/ember_rex/*.json, and the runner's own
  outputs (improved/ember_rex*, improved/textures/ember_rex_*, rigged/ember_rex*, renders/ember_rex/*,
  improve/log_ember_rex_*, improved/check_ember_rex.json, rigged/check_ember_rex.json).
  The scaffolded improve/ember_rex_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/ember_rex/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/ember_rex_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/ember_rex_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/ember_rex/ember_rex_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init ember_rex   (writes the stub improve/ember_rex_build.py, improve/ember_rex_finish.json,
     palettes/ember_rex/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/ember_rex_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate ember_rex and python -P improve/proxy_finish.py panel ember_rex.
  6. Fill improve/ember_rex_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS (ember_rex v1.1 ONLY: improve/proxy_finish_light.py for the opt-in light.mirror_copy op) PLUS improve/ember_rex_finish.json and improve/ember_rex_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/ember_rex/ember_rex_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): generated from back + side only (the front is drawn 3/4); the gate failed on
side length (+8.9 %): use `fit_side_length`. The front IoU 0.54 is the 3/4 front, not a defect. The proxy reads darker
than the sheet (dark red-brown where the sheet is warm red / orange with bright flame tips 248,127,74 / 253,197,109)
and its legs read near-black where the sheet draws scaled brown (70,52,54): check the texture, tint only. Glow only the
small chest ember gem. Crest / beak / eye as generated.
ORCHESTRATOR FOLLOW-UP (v1.1, after the v1 report): the proxy has ONE leg (+x). Retries did not fix it: forced
front+back+side (sha 6345a898, mean 0.72, blocky crest; kept in scratch ember_rex_try2) and seeds 7 / 101 (scratch
ember_rex_seed7 / _seed101, 0.85 / 0.84) all generate one leg, because the sheet's back and side views show one.
The proxy stays sha cbbd25d9 (restored). Fix by MIRROR-COPYING the existing leg:
- add an OPT-IN op `light.mirror_copy` to improve/proxy_finish_light.py (allowlist extended to that file for this op
  ONLY): proxy triangles inside a box are copied, reflected across `midline_x` (x' = 2*mid - x), and join the surface
  before the watertight remesh, carrying their texture (the bake samples the proxy + the copied triangles). Absent
  key = no change: prove it by rerunning ONE other light unit's runner (frost_calf) and quoting its DIGEST unchanged
  (362f5f93d32ac747).
- box: the +x leg from the floor to the hip, excluding the tail feathers; the copy lands where the sheet puts the
  second leg (the side view's stagger is the sheet's pose; the rest pose places the copy mirror-symmetric).
- the rig's -x leg chain then follows the copy. Write a feet / legs close-up (front + back + side).

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/ember_rex_finish.json, improve/ember_rex_STATE.md, renders/ember_rex/ember_rex_finish_panel.png.
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
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/ember_rex_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/ember_rex/ember_rex_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): Artist: 'ember rex tail is moved to one side, can we rig it so we can position it in the center or the side'. Symmetric rest pose: the TAIL rests CENTRED on the midline (to_points along the sheet's side-view tail curve at x = 0, or heading +y), the head straight (v1: tail swung to -x, mirror p99 0.92 u), legs already mirror-copied. The rig must have a TAIL CHAIN (several bones) so the tail can be posed to either side later - report its bone names. Keep the legs' light.mirror_copy. Show the tail centred (back + top views) and one test pose with the tail swung to the side via the rig (a still render of the posed rig, not baked).
