LANE: duskmaw_true_form v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/duskmaw_true_form_STATE.md. SPEC: design/review-log.md entry "2026-10-24 — NEW UNIT: Duskmaw re-run, two forms" (artist
verbatim + binding reading). SHEET: design/reference/duskmaw_true_form-character-sheet.webp — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/duskmaw_true_form/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 6 framed panel(s), 354 title blob(s) and 79 rule line(s) before the split (was --region)
  - dark subject on a dark sheet at x 0-1091 (the threshold lost 50% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (2907 px)
  - views fused by touching parts: split at x 445,775 by a watershed along the weakest contact (the plain-column equivalent is --cuts 445,775; --assign / --cuts override)
  - back axis 622.0 px (mirror-symmetry of the body mass); the feet-base rule says 609.5
  - side floor 684 px = the lowest body contact; ignored a drop shadow (1411 px), a part with no leg-like support hanging 2 px lower (tail curl / pool / unpaired tail) (raw lowest row 686)
  - side axis 886.5 px (centre of the floor contact span); the feet-base rule says 912.5
  - front and back body widths disagree (0.69, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - side view faces image-right (auto, colour score +0.21) -> meshgen view 'right'
  - front-vs-side height: front 5.43 u, side 5.69 u (+4.8%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - reliable (mean IoU 0.77, side length -1.8%, sha 09e4004f, seed 56); panel: design/reference/duskmaw_true_form/proxy/duskmaw_true_form_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/duskmaw_true_form_build.py + improve/duskmaw_true_form_parts.py + palettes/duskmaw_true_form/default.json (+ any
palettes/duskmaw_true_form/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: the TRUE FORM of a two-form unit (Shadow Abyss - True Form; spec section "TRUE FORM" of the review-log entry): a decoy crown head with two glowing yellow eyes on top of a massive hollow open MAW (red lips, layers of cream fangs, dark ridged interior), large reaching tendril arms with red claw ends, a tendril base; jaw rig for the mouth states; light flow. Glow = decoy eyes only.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/duskmaw_true_form_build.py, improve/duskmaw_true_form_parts.py, palettes/duskmaw_true_form/*.json, and the runner's own
  outputs (improved/duskmaw_true_form*, improved/textures/duskmaw_true_form_*, rigged/duskmaw_true_form*, renders/duskmaw_true_form/*,
  improve/log_duskmaw_true_form_*, improved/check_duskmaw_true_form.json, rigged/check_duskmaw_true_form.json).
  The scaffolded improve/duskmaw_true_form_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/duskmaw_true_form/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:/Users/kenne/AppData/Local/Temp/claude/C--Users-kenne-OneDrive-Desktop-git-forge/da2aa1fe-9826-4887-a170-2ed983961ce7/scratchpad/duskmaw_true_form_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/duskmaw_true_form_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/duskmaw_true_form/duskmaw_true_form_sheet_vs_model.png (where the
outline departs from the sheet, by view, and why); which renders exist; the TUNABLE knobs an artist is most
likely to nudge; every simplification vs the sheet.

STOP: done = runner green except report-only checks + renders written + GATE lines quoted + report. Blocked = a
kit/tool defect outside the allowlist (report it, don't patch it). Grown = a second deliverable appears (report
the split). Never commit, never spawn agents, never launch anything windowed, never touch port 9876.

==== LIGHT FLOW OVERRIDE (this section WINS over the procedural DELIVERABLE / REFERENCE IMPLEMENTATION above) ====
The artist prefers the Pixal3D proxy over hand builds, and a heavy finish broke a face before (gemwing: "in trying to
fix the symmetry and make changes we broke some things, like the mouth"). This unit ships on the LIGHT finish like
vulture_creature, crystal_canine and the 2026-10-16 batch (read improve/vulture_creature_finish.json + its STATE,
improve/drakescale_cub_finish.json + its STATE, docs/conquest-new-unit-flow.md sections 5 + 5b first; copy their idioms).
PROXY NOTE (orchestrator): the proxy was generated from the sheet's FRONT + SIDE only (sha 09e4004f). The 3-view run let
the maw's red interior show through the BACK (kept for reference in design/reference/duskmaw_true_form/proxy_3view_seed56/).
This proxy's back is closed shadow like the sheet, but WIDER than the sheet's back view (the sheet draws its back ~0.76x
the front's width and 5 % shorter - an inconsistent drawing): expect a low back IoU (~0.54) and report it, do not chase it.
  1. python -P improve/proxy_finish.py init duskmaw_true_form. "mode": "light", tier regular [8000, 22000] (if the many
     tendrils + teeth need more, up to the top of the window; report), body_tris ~20000.
  2. Tune ONLY what the light flow needs: `refloor` (floor at the tendrils' ground contact; rescale to the sheet front
     height 5.43 u), `light.dense` boxes for the DECOY HEAD (eyes), the TEETH rows and the claw tips; GLOW only on the
     decoy head's two yellow eyes (252,234,167, vulture_creature eye idiom; paint crisp slanted eyes if the proxy's are
     faint). The maw is NOT emissive. Teeth cream (252,234,167 / the sheet's tooth tone), lips + interior reds
     (201,60,62 / 122,43,49), body near-black matte (42,41,46 .. 69,71,84): tint / recolour only for a visible defect.
  3. NEVER on the light flow: geometry symmetrise, straighten, part replacement or cuts on the head / maw. Symmetric rest
     pose by `repose` (bones) only if an arm pair is visibly off; report mirror p99.
  4. MOUTH (the sheet's MOUTH STATES panel: OPEN = rest/luring = the turnaround, CHOMP = half shut with the fangs
     interlocking, CLOSED = shut): rig the maw with jaw bones - upper-jaw (lips + upper fang rows, the decoy head rides on
     it) and lower-jaw (lower lip + lower fangs) - weighted so a jaw-close moves the lip rims toward each other. Rest =
     OPEN. If the hollow generated maw cannot close without tearing / self-intersecting badly, report the max closure you
     reach (how far the lips get) and the evidence; do not rebuild the maw.
  5. CLIPS (placeholder): `idle` (tendril sway + a slow breathing of the maw, open), `walk` (a GLIDE placeholder: slides,
     tendrils trail - the artist has not said how the true form moves), `chomp` (~24 f: open -> shut -> open, the
     attack), `close` (~16 f to the CLOSED state, held), and `transform_in` (~48 f, NOT looping: rises up out of its
     tendrils from the floor to the rest pose - the game swaps from the base `duskmaw` at frame 0 under a VFX). Report any
     clip the kit cannot make.
  6. Full run: powershell -NoProfile -File improve/duskmaw_true_form_run.ps1 (background, wait). Then python -P
     improve/proxy_finish.py gate duskmaw_true_form and python -P improve/proxy_finish.py panel duskmaw_true_form. Also
     render the three mouth states (open / chomp mid / closed) front 3/4 side by side next to the sheet's MOUTH STATES
     panel: renders/duskmaw_true_form/duskmaw_true_form_mouth_states.png.
  7. Fill improve/duskmaw_true_form_STATE.md (vulture_creature STATE style; v1 = Pixal3D light flow front+side, sha 09e4004f).
FILES on the light flow: the allowlist above PLUS improve/duskmaw_true_form_finish.json and improve/duskmaw_true_form_STATE.md
(do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT with the proposed patch).
EVIDENCE adds: the proxy_finish gate numbers (vs sheet AND vs the raw proxy, mirror p99, open edges, tris), the panel
renders/duskmaw_true_form/duskmaw_true_form_finish_panel.png and your read (decoy eyes read? teeth intact? anything
melted?), the mouth-states render.
SIBLING: `duskmaw` (the base form, another lane running in parallel) - do not touch its files. Same palette family,
same facing (-Y), same origin at the floor centre.
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
