LANE: spike_beak_2 v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/spike_beak_2_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section SPIKE BEAK 2) (artist
verbatim + binding reading). SHEET: design/reference/spike_beak_2-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/spike_beak_2/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 3 framed panel(s), 82 title blob(s) and 306 rule line(s) before the split (was --region)
  - views fused by touching parts: split at x 783 by a watershed along the weakest contact (the plain-column equivalent is --cuts 401,783; --assign / --cuts override)
  - front floor 755 px = the lowest body contact; ignored a part with no leg-like support hanging 16 px lower (tail curl / pool / unpaired tail) (raw lowest row 771)
  - back floor 744 px = the lowest body contact; ignored a part with no leg-like support hanging 5 px lower (tail curl / pool / unpaired tail) (raw lowest row 749)
  - side floor 780 px = the lowest body contact; ignored a part with no leg-like support hanging 3 px lower (tail curl / pool / unpaired tail) (raw lowest row 783)
  - side axis 1005.0 px (centre of the floor contact span); the feet-base rule says 978.5
  - side view faces image-left (flag, colour score -0.30) -> meshgen view 'left'
  - front-vs-side height: front 5.90 u, side 5.99 u (+1.5%)
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.92, side length +0.6%, sha 793ce80b, seed 56); panel: design/reference/spike_beak_2/proxy/spike_beak_2_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/spike_beak_2_build.py + improve/spike_beak_2_parts.py + palettes/spike_beak_2/default.json (+ any
palettes/spike_beak_2/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: round blue bird biped (the mid Spike Beak, a smaller simpler Spike Beak 3); light flow (proxy as base) WITH a symmetric rest pose by repose. No glow.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/spike_beak_2_build.py, improve/spike_beak_2_parts.py, palettes/spike_beak_2/*.json, and the runner's own
  outputs (improved/spike_beak_2*, improved/textures/spike_beak_2_*, rigged/spike_beak_2*, renders/spike_beak_2/*,
  improve/log_spike_beak_2_*, improved/check_spike_beak_2.json, rigged/check_spike_beak_2.json).
  The scaffolded improve/spike_beak_2_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/spike_beak_2/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/spike_beak_2_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/spike_beak_2_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/spike_beak_2/spike_beak_2_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init spike_beak_2   (writes the stub improve/spike_beak_2_build.py, improve/spike_beak_2_finish.json,
     palettes/spike_beak_2/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/spike_beak_2_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate spike_beak_2 and python -P improve/proxy_finish.py panel spike_beak_2.
  6. Fill improve/spike_beak_2_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/spike_beak_2_finish.json and improve/spike_beak_2_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/spike_beak_2/spike_beak_2_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): proxy reliable (mean IoU 0.92, sha 793ce80b). Artist: "spike beak 2 is
essentially a smaller simpler version of 3" - and the artist's notes on Spike Beak 3 apply in spirit (design/review-log.md
"2026-10-17 — Spike Beak 3 v1 notes": the eyes must read, the mask/spike must read as one clean yellow shape).
(1) TONE: the proxy reads dark navy and dull; the sheet is bright layered blues (205,210,222 / 130,158,199 / 72,117,188 /
47,82,148 / 39,57,103) with clear WHITE accents (the chest V, the collar band, wing-edge and tail tips). Recolour3d by
rank (improve/azure_scale_mage_finish.json method) so the blues and the white pattern match the sheet; the white must
read as white, not grey-blue. (2) BEAK / MASK / SPIKE: one clean yellow (215,170,94) shape; the proxy's beak reads
brownish and droops a little - recolour; reshape only if it clearly departs from the sheet's straight beak (report).
(3) EYES: LARGE, expressive (cream ring, dark navy iris, small highlight) - crisp paint inside the eye's own outline
(eye_rim / eye_iris idiom, improve/frost_giant_child_finish.json); they must read clearly in the front and side.
(4) SYMMETRIC REST POSE (artist 2026-10-17): repose per improve/storm_bear_finish.json (legs symmetric + planted, head
straight with a rigid face box); never symmetrise geometry. Close-ups: head front + side.
