LANE: vampwarrior v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/vampwarrior_STATE.md. SPEC: design/review-log.md entry "2026-10-24 — NEW UNIT: Vampire Warrior re-run" (artist
verbatim + binding reading). SHEET: design/reference/vampwarrior/vampwarrior-sheet-proxyprep.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/vampwarrior/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - dark subject on a dark sheet at x 30-442; 442-790; 790-1015 (the threshold lost 50%, 63%, 58% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (32940, 51793, 33065 px)
  - front floor 907 px = the lowest body contact; ignored a drop shadow (44 px), a part with no leg-like support hanging 10 px lower (tail curl / pool / unpaired tail), 1 narrow end contact(s) (tail tip) (raw lowest row 918)
  - back axis 569.5 px (mirror-symmetry of the body mass); the feet-base rule says 590.5
  - side floor 901 px = the lowest body contact; ignored a drop shadow (53 px), a part with no leg-like support hanging 4 px lower (tail curl / pool / unpaired tail) (raw lowest row 907)
  - side axis 914.5 px (centre of the floor contact span); the feet-base rule says 901.0
  - front and back body widths disagree (0.70, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - side view faces image-left (flag, colour score -0.03) -> meshgen view 'left'
  - front-vs-side height: front 8.08 u, side 7.92 u (-2.0%)
  - orchestrator 2026-10-24: the near-black cape matches the sheet grey (39,39,39), so the enclosed-background carve cut ~33-52k px holes out of the cape. Fix: view_masks = the tol-26 labels UNION a --bg-tol 6 pass, and `sheet` = vampwarrior-sheet-proxyprep.png (the sheet with R+5 on 202528 in-silhouette near-background pixels, so the carve no longer treats cloth as background; real pockets outside the silhouette still carve). Original: design/reference/vampwarrior-character-sheet.png
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.81, side length +2.4%, sha b8352fbf, seed 56); panel: design/reference/vampwarrior/proxy/vampwarrior_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/vampwarrior_build.py + improve/vampwarrior_parts.py + palettes/vampwarrior/default.json (+ any
palettes/vampwarrior/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: humanoid biped (tall female vampire warrior: long hair, torn cape, armoured bodice, thigh boots) + a separate rigid point-down leaf sword on a hand bone; light flow (proxy as base) with the sword replaced by a closed procedural part. No glow.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/vampwarrior_build.py, improve/vampwarrior_parts.py, palettes/vampwarrior/*.json, and the runner's own
  outputs (improved/vampwarrior*, improved/textures/vampwarrior_*, rigged/vampwarrior*, renders/vampwarrior/*,
  improve/log_vampwarrior_*, improved/check_vampwarrior.json, rigged/check_vampwarrior.json).
  The scaffolded improve/vampwarrior_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/vampwarrior/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:/Users/kenne/AppData/Local/Temp/claude/C--Users-kenne-OneDrive-Desktop-git-forge/da2aa1fe-9826-4887-a170-2ed983961ce7/scratchpad/vampwarrior_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/vampwarrior_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/vampwarrior/vampwarrior_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.

==== LIGHT FLOW OVERRIDE (this section WINS over the procedural DELIVERABLE / REFERENCE IMPLEMENTATION above) ====
The artist prefers the Pixal3D proxy over hand builds, and a heavy finish broke a face before (gemwing: "in trying to
fix the symmetry and make changes we broke some things, like the mouth"). This unit ships on the LIGHT finish like
vulture_creature, crystal_canine and the 2026-10-16 batch (read improve/vulture_creature_finish.json + its STATE,
improve/iron_scale_knight_finish.json + its STATE (a humanoid with eye paint), docs/conquest-new-unit-flow.md sections
5 + 5b first; copy their idioms). It REPLACES the procedural v4.2 vampwarrior (improve/vampwarrior_build.py is the old
build; a copy of it + vampwarrior_parts.py is in your scratch folder; v4.2 is in git).
INTAKE NOTE (orchestrator): the near-black cape matches the sheet grey, so intake.json `sheet` points at
design/reference/vampwarrior/vampwarrior-sheet-proxyprep.png (the original sheet with R+5 on in-silhouette near-background
pixels, so the kit's enclosed-background carve stops cutting the cape) and view_masks.png is a union mask. Colour work
samples the ORIGINAL sheet (intake.json `sheet_original`). Proxy sha b8352fbf (front+back+side, seed 56): mean 0.81;
back 0.69 (the sheet draws the back ~0.77x the front's width: an inconsistent drawing - report, do not chase).
  1. python -P improve/proxy_finish.py init vampwarrior (overwriting the old build/palette is expected; force if it
     refuses). "mode": "light", tier regular [8000, 22000], body_tris ~20000 incl. the sword.
  2. FACE (most important, never geometry-edit it): `light.dense` on the head / face / hair front strands. Then check the
     face against the sheet's face panel + design/reference/vampwarrior-v4-face-reference.png and the binding artist notes
     in the review-log entry "2026-10-24 — NEW UNIT: Vampire Warrior re-run": warm pale grey-mauve skin (not Blender grey),
     red eyes (darker, less bright), thick brows, faint lip red, small fangs from UNDER the upper lip (paint only; no fang
     root at the lip edge). Paint (recolour3d after the clamp) only where the proxy's face is lost or wrong, with a
     close-up before / after; if the proxy face is crooked or melted, report it with a close-up (the orchestrator decides
     on a re-generation) - do not reshape it.
  3. SWORD: the proxy's sword is a dark flat board fused to the hand. Replace it with a CLOSED separate rigid mesh: the
     sheet's SWORD DETAIL view (x ~1062-1178 px, point-down, nearly her height): leaf / flame-shaped serrated bone-pale
     blade (177,164,156 with a dark centre vein), simple dark handle with silver rings, red tassel at the guard. The old
     build's parts.sword(cfg) (scratch copy) already makes this shape procedurally - reuse it (copy the function into
     improve/vampwarrior_parts.py, which you own) via the finish config's closed-parts path (proxy_finish_parts / light
     parts or cuts + adds; see docs section 5 "Parts"). Cut the proxy's board sword out (light.cuts, boxes clear of the
     hand / cape), keep the hand. Sword on its own bone parented to the right hand, held point-down where the sheet
     front draws it (her right hand, image-left), tip near the floor. If the kit has no way to add a closed part on the
     light flow, report the gap + proposed patch.
  4. CAPE: the sheet's cape lining is dark red (79,49,52 / 44,33,35) and the proxy lost much of it to grey: tint only
     (recolour3d / tint_clamp) toward the sheet's lining where the sheet shows red (inner faces, the torn hem); the
     outer cape near-black (33,28,28 / 53,49,49). Hair bone-white (177,164,156) - keep.
  5. NEVER on the light flow: geometry symmetrise, straighten, part replacement or cuts on the head / face. Symmetric rest
     pose: only by `repose` (bones, rigid face box) if a limb pair is visibly off; the sword arm stays where the sheet
     holds it (report the asymmetry with mirror p99).
  6. CLIPS (placeholder, keep the contract names the game imports: `idle`, `walk`): biped idle with hair / cape
     follow-through (the artist's standing note: no biped stiffness), biped walk. The sword moves with the hand.
  7. Full run: powershell -NoProfile -File improve/vampwarrior_run.ps1 (background, wait). Then python -P improve/
     proxy_finish.py gate vampwarrior and python -P improve/proxy_finish.py panel vampwarrior --hand <the old v4.2 rigged
     blend: copy rigged/vampwarrior.blend to your scratch BEFORE your run overwrites it> so the artist sees
     sheet | proxy | new | old v4.2. Close-ups: face front + 3/4 next to the sheet's face panel, the sword full length
     next to the SWORD DETAIL view.
  8. Fill improve/vampwarrior_STATE.md (vulture_creature STATE style; v5 = Pixal3D light flow, sha b8352fbf).
FILES on the light flow: the allowlist above PLUS improve/vampwarrior_finish.json and improve/vampwarrior_STATE.md (do not
hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT with the proposed patch).
EVIDENCE adds: the proxy_finish gate numbers (vs sheet AND vs the raw proxy, mirror p99, open edges, tris), the panel
renders/vampwarrior/vampwarrior_finish_panel.png and your read (face intact and on-model? sword reads? cape red?).
Two other lanes (duskmaw, duskmaw_true_form) may be running in parallel: do not touch their files.
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
