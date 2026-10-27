LANE: duskmaw v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/duskmaw_STATE.md. SPEC: design/review-log.md entry "2026-10-24 — NEW UNIT: Duskmaw re-run, two forms" (artist
verbatim + binding reading). SHEET: design/reference/duskmaw-character-sheet.webp — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/duskmaw/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 7 framed panel(s), 95 title blob(s) and 50 rule line(s) before the split (was --region)
  - dark subject on a dark sheet at x 44-430; 482-1139 (the threshold lost 26%, 38% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (1896, 1347 px)
  - views fused by touching parts: split at x 435,791 by a watershed along the weakest contact (the plain-column equivalent is --cuts 435,791; --assign / --cuts override)
  - front floor 840 px = the lowest body contact; ignored a drop shadow (569 px), a part with no leg-like support hanging 4 px lower (tail curl / pool / unpaired tail) (raw lowest row 844)
  - back floor 837 px = the lowest body contact; ignored a drop shadow (1167 px), a part with no leg-like support hanging 4 px lower (tail curl / pool / unpaired tail) (raw lowest row 841)
  - side floor 832 px = the lowest body contact; ignored a drop shadow (1230 px), a part with no leg-like support hanging 9 px lower (tail curl / pool / unpaired tail), 1 narrow end contact(s) (tail tip) (raw lowest row 841)
  - side axis 956.5 px (centre of the floor contact span); the feet-base rule says 969.5
  - side view faces image-right (flag, colour score +0.00) -> meshgen view 'right'
  - front-vs-side height: front 7.13 u, side 7.33 u (+2.8%)
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.78, side length +1.9%, sha ecb90666, seed 56); panel: design/reference/duskmaw/proxy/duskmaw_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/duskmaw_build.py + improve/duskmaw_parts.py + palettes/duskmaw/default.json (+ any
palettes/duskmaw/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: the BASE form of a two-form unit (Shadow Abyss): tall slender shadow humanoid, crown head with red-tipped spikes, two glowing yellow eyes, a glowing jagged TEAR in the torso, long clawed arms, a tendril base flowing along the floor; light flow (proxy as base). Glow = eyes + torso tear. Re-run of the old procedural duskmaw v4 (superseded).

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/duskmaw_build.py, improve/duskmaw_parts.py, palettes/duskmaw/*.json, and the runner's own
  outputs (improved/duskmaw*, improved/textures/duskmaw_*, rigged/duskmaw*, renders/duskmaw/*,
  improve/log_duskmaw_*, improved/check_duskmaw.json, rigged/check_duskmaw.json).
  The scaffolded improve/duskmaw_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/duskmaw/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:/Users/kenne/AppData/Local/Temp/claude/C--Users-kenne-OneDrive-Desktop-git-forge/da2aa1fe-9826-4887-a170-2ed983961ce7/scratchpad/duskmaw_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/duskmaw_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/duskmaw/duskmaw_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.

==== LIGHT FLOW OVERRIDE (this section WINS over the procedural DELIVERABLE / REFERENCE IMPLEMENTATION above) ====
The artist prefers the Pixal3D proxy over hand builds ("all of the work it did looks better than what we did"), and a
heavy finish broke a face before (gemwing: "in trying to fix the symmetry and make changes we broke some things, like
the mouth"). This unit ships on the LIGHT finish, like the shipped units vulture_creature and crystal_canine and the
2026-10-16 batch (read improve/vulture_creature_finish.json + improve/vulture_creature_STATE.md, improve/
drakescale_cub_finish.json + its STATE, and docs/conquest-new-unit-flow.md sections 5 + 5b first; copy their idioms):
  1. python -P improve/proxy_finish.py init duskmaw (writes the stub improve/duskmaw_build.py, improve/duskmaw_finish.json,
     palettes/duskmaw/default.json). improve/duskmaw_build.py currently holds the OLD procedural v4 build (superseded,
     kept in git at 6b9e2e1): overwriting it is expected (use init's force flag if it refuses). Set "mode": "light",
     tier regular [8000, 22000], body_tris ~18000-20000.
  2. Tune ONLY what the light flow needs: `refloor` (floor at the base tendrils' ground contact, not a curl tip; rescale
     to the sheet front height 7.13 u), `light.dense` boxes so the HEAD / CROWN, the CLAW tips and the TORSO TEAR keep
     their detail, and GLOW only where the sheet draws it: the two yellow EYES (253,223,149 core; the face around them
     stays shadow-dark) and the TORSO TEAR's inner orange -> yellow glow (254,136,75 -> 253,223,149, red 170,52,57 rim
     NOT emissive) - via light.glow3d + palette emission regions exactly as vulture_creature's eyes. The proxy's eyes are
     faint: paint crisp slanted almond eyes on the face surface at the sheet's front-view eye positions (front px
     ~232 / ~262, mirrored about x = 0), mirrored design on both (recolour3d after_clamp idiom). Red tips on the crown
     spikes, shoulder spikes and claws must read red (170,52,57); the body is matte near-black (37,36,41 .. 62,58,69):
     tint_clamp / recolour3d to kill the proxy's glossy look if the bake carries highlights.
  3. NEVER on the light flow: geometry symmetrise, straighten, part replacement or cuts on the head / face. Symmetric
     rest pose: the sheet's pose is already near-symmetric; if the arms or head are visibly off, use `repose` (bones,
     rigid face box), never geometry mirroring; report mirror p99 before / after.
  4. If the proxy is visibly wrong (a melted / fused part the sheet clearly draws), report it with the panel and finish
     the run anyway. Do NOT regenerate the proxy (the GPU and design/reference/ belong to the orchestrator); say which
     views a retry should use.
  5. CLIPS (placeholder, keep the contract names the game already imports for duskmaw: `idle`, `walk`): idle = slow
     hover-sway of the tendrils + a breathing scale of the torso tear (no chomp: the new tear has no teeth); walk = the
     artist's answered GLIDE (2026-09-25: the body slides, tendrils trail; no stepping). ADD `transform_out` (~48 f, NOT
     looping): the body sinks down into its tendrils (scale z toward the floor, tendrils flare out) - the game swaps to
     duskmaw_true_form at the clip's last frame under a VFX. If the clip kit only supports idle/walk, report the gap.
  6. Full run: powershell -NoProfile -File improve/duskmaw_run.ps1 (background, wait). Then python -P improve/
     proxy_finish.py gate duskmaw and python -P improve/proxy_finish.py panel duskmaw --hand <the old v4 rigged blend if
     still at rigged/duskmaw.blend BEFORE your run overwrites it: copy it to your scratch first> so the artist sees
     sheet | proxy | new | old v4.
  7. Fill improve/duskmaw_STATE.md in the vulture_creature STATE style (version v5 = Pixal3D light flow; proxy sha
     ecb90666; what the finish changed; knobs; open items).
FILES on the light flow: the allowlist above PLUS improve/duskmaw_finish.json and improve/duskmaw_STATE.md (the stub build
is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT with the proposed patch).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/duskmaw/duskmaw_finish_panel.png and your read of it (eyes read? tear glows? red tips red?).
Close-ups: head front + side (eyes), torso tear front, a claw hand.
SIBLING: duskmaw_true_form (a separate unit, another lane) is the same creature's second form; do not touch its files.
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
