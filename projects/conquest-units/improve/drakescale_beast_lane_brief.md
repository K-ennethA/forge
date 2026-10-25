LANE: drakescale_beast v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/drakescale_beast_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section DRAKESCALE BEAST) (artist
verbatim + binding reading). SHEET: design/reference/drakescale_beast-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/drakescale_beast/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 5 framed panel(s), 155 title blob(s) and 190 rule line(s) before the split (was --region)
  - dark subject on a dark sheet at x 0-1185 (the threshold lost 13% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (0 px)
  - views fused by touching parts: split at x 334,624 by a watershed along the weakest contact (the plain-column equivalent is --cuts 334,624; --assign / --cuts override)
  - front axis 178.5 px (mirror-symmetry of the body mass); the feet-base rule says 162.0
  - back floor 834 px = the lowest body contact; ignored a part with no leg-like support hanging 1 px lower (tail curl / pool / unpaired tail) (raw lowest row 835)
  - back axis 469.0 px (mirror-symmetry of the body mass); the feet-base rule says 624.5
  - side floor 826 px = the lowest body contact; ignored a drop shadow (107 px), a part with no leg-like support hanging 1 px lower (tail curl / pool / unpaired tail) (raw lowest row 827)
  - side axis 1102.5 px (centre of the floor contact span); the feet-base rule says 1050.5
  - probable 3/4 / perspective view(s): back (mirror IoU 0.72, tone asymmetry 0.44) -> ortho_views front,side (a 3/4 SIDE is not auto-detected: check it)
  - side view faces image-left (auto, colour score -0.16) -> meshgen view 'left'
  - front-vs-side height: front 6.28 u, side 6.63 u (+5.6%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.89, side length -0.0%, sha 197465d1, seed 56); panel: design/reference/drakescale_beast/proxy/drakescale_beast_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/drakescale_beast_build.py + improve/drakescale_beast_parts.py + palettes/drakescale_beast/default.json (+ any
palettes/drakescale_beast/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: quadruped pangolin-dragon; light flow (proxy as base). No glow (the orange eye is painted). Keep the head, horn scales and eye dense.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/drakescale_beast_build.py, improve/drakescale_beast_parts.py, palettes/drakescale_beast/*.json, and the runner's own
  outputs (improved/drakescale_beast*, improved/textures/drakescale_beast_*, rigged/drakescale_beast*, renders/drakescale_beast/*,
  improve/log_drakescale_beast_*, improved/check_drakescale_beast.json, rigged/check_drakescale_beast.json).
  The scaffolded improve/drakescale_beast_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/drakescale_beast/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/drakescale_beast_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/drakescale_beast_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/drakescale_beast/drakescale_beast_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init drakescale_beast   (writes the stub improve/drakescale_beast_build.py, improve/drakescale_beast_finish.json,
     palettes/drakescale_beast/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/drakescale_beast_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate drakescale_beast and python -P improve/proxy_finish.py panel drakescale_beast.
  6. Fill improve/drakescale_beast_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/drakescale_beast_finish.json and improve/drakescale_beast_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/drakescale_beast/drakescale_beast_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): body / head good (mean IoU 0.89; back left out as 3/4). Two items:
- TAIL: the sheet draws a LONG powerful scaled tail sweeping down and back to the floor (side view, past the hind
  legs); the proxy's tail is short and stubby, and the back view shows almost none. The tail is not the face: you MAY
  extend / rebuild it (light.adds tapered capsule chain along the sheet's side-view tail silhouette, painted with the
  surrounding scale texture or the sheet's scale tones), one opt-in region with a "why".
- TONE: the proxy reads dark olive / khaki where the sheet is warm brown scales (72,57,48 / 124,87,65) with copper
  tips (188,146,115) and a cream belly (220,187,156): check the texture, tint only (recolour3d / tint_clamp).
Head / horns / eye as generated.

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/drakescale_beast_finish.json, improve/drakescale_beast_STATE.md, renders/drakescale_beast/drakescale_beast_finish_panel.png.
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
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/drakescale_beast_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/drakescale_beast/drakescale_beast_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): (a) Artist 2026-10-17 'Drakescale Beast v1 head note' in design/review-log.md: the head is 'way too compressed and looks smooshed where we cant see eyes or any other face details' (LOOK at design/reference/drakescale_beast-v1-head-annotation.png and the sheet's HEAD DETAIL panel). Three re-seeds (scratch drake_seed7/101/23) all gave the same crushed snout. The orchestrator generated a HEAD-ONLY Pixal3D mesh from the sheet's head-detail panel (upscaled x4, BiRefNet matte, seed 56): design/reference/drakescale_beast/head_graft/head_pixal3d.glb (sha in the folder; renders head_views.png): a long snout, clear orange eye, cream jaw, swept horn scales - but generated from ONE side, so its far side / back is a flat cut. GRAFT it: add an opt-in op light.head_graft to improve/proxy_finish_light.py (allowlist extended for this op only; absent key = no change, prove it with frost_calf's DIGEST 362f5f93d32ac747): (1) load the head glb, orient it to the body frame (snout -y, up +z), keep only its GOOD half (the side the sheet drew, up to its midline plane) and MIRROR it across x = 0 to make a symmetric head (this is the head-only symmetric rebuild the artist asked for; the face is the generated one, mirrored, never re-sculpted); (2) scale + place it to the sheet's side-view head size and position (snout tip, eye, crest heights from sheet px); (3) cut the body proxy's own head with light.cuts down to the neck and union the graft before the watertight remesh so the neck blends (smooth union, the neck's dorsal scales kept); (4) carry the graft's texture into the bake like light.mirror_copy does (see its implementation). Dense box on the new head. (b) Symmetric rest pose (v1 mirror p99 0.96 u: staggered legs, nose-down pitch, hind paws hovering): repose legs symmetric + planted, head/neck straight, and the TAIL: v1 rebuilt it as a smooth banded tube that read poorly - keep the tail but put it on a centred tail chain; if the proxy's own scaled tail can be reposed onto the sheet's curve instead (to_points), prefer that and drop the tube (report which). Close-ups: head side + 3/4 + front vs the sheet's head panel.

==== V2.1 (2026-10-17, artist tail note; WINS over V2 for the tail only) ====
SPEC: design/review-log.md "2026-10-17 — Drakescale Beast v2 tail note" (LOOK at the annotation image). Remove the
capsule tube tail (its light.adds + paint bands). Build the tail by DUPLICATING the proxy's own scaled tail: add ONE
opt-in op `light.segment_array` to improve/proxy_finish_light.py (absent key = no change; prove it with frost_calf's
DIGEST 362f5f93d32ac747; keep the other opt-in ops intact): source = the proxy triangles in a box around the generated
tail ROOT segment (the scaled section just behind the hips, ~0.6-0.9 u long); N copies placed end to end along a
centreline (the sheet's side-view tail curve at x = 0, sheet px -> units as the v1 tail config did), each copy rotated
to the curve tangent and uniformly scaled by a taper factor (e.g. 0.88 per copy) so the tail narrows; the LAST copy
squashed to a POINT (scale the cross-section to ~0 at the tip). Copies carry their texture into the bake like
light.mirror_copy (append reflected/transformed triangles with their UVs; a smooth-union seam between copies before the
remesh). Put the tail on the existing centred tail chain. Close-ups: tail side, back 3/4, top; and the whole beast side.
