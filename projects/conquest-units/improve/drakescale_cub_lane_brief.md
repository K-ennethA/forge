LANE: drakescale_cub v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/drakescale_cub_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section DRAKESCALE CUB) (artist
verbatim + binding reading). SHEET: design/reference/drakescale_cub-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/drakescale_cub/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 5 framed panel(s), 135 title blob(s) and 162 rule line(s) before the split (was --region)
  - dark subject on a dark sheet at x 0-1170 (the threshold lost 12% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (0 px)
  - views fused by touching parts: split at x 319,591 by a watershed along the weakest contact (the plain-column equivalent is --cuts 319,591; --assign / --cuts override)
  - front axis 176.5 px (mirror-symmetry of the body mass); the feet-base rule says 157.5
  - back axis 456.0 px (mirror-symmetry of the body mass); the feet-base rule says 630.5
  - side axis 1090.5 px (centre of the floor contact span); the feet-base rule says 1057.0
  - front and back body widths disagree (0.76, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - probable 3/4 / perspective view(s): back (mirror IoU 0.73, tone asymmetry 0.37) -> ortho_views front,side (a 3/4 SIDE is not auto-detected: check it)
  - side view faces image-left (flag, colour score -0.17) -> meshgen view 'left'
  - front-vs-side height: front 5.87 u, side 5.29 u (-9.9%) - over 3 %: reconcile them and say how
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by +8.4%) (mean IoU 0.86, side length +8.4%, sha 57ad4b2d, seed 56); panel: design/reference/drakescale_cub/proxy/drakescale_cub_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/drakescale_cub_build.py + improve/drakescale_cub_parts.py + palettes/drakescale_cub/default.json (+ any
palettes/drakescale_cub/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: quadruped pangolin-dragon cub (young Drakescale Beast); light flow (proxy as base) WITH a symmetric rest pose by repose. No glow.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/drakescale_cub_build.py, improve/drakescale_cub_parts.py, palettes/drakescale_cub/*.json, and the runner's own
  outputs (improved/drakescale_cub*, improved/textures/drakescale_cub_*, rigged/drakescale_cub*, renders/drakescale_cub/*,
  improve/log_drakescale_cub_*, improved/check_drakescale_cub.json, rigged/check_drakescale_cub.json).
  The scaffolded improve/drakescale_cub_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/drakescale_cub/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/drakescale_cub_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/drakescale_cub_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/drakescale_cub/drakescale_cub_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init drakescale_cub   (writes the stub improve/drakescale_cub_build.py, improve/drakescale_cub_finish.json,
     palettes/drakescale_cub/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/drakescale_cub_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate drakescale_cub and python -P improve/proxy_finish.py panel drakescale_cub.
  6. Fill improve/drakescale_cub_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/drakescale_cub_finish.json and improve/drakescale_cub_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/drakescale_cub/drakescale_cub_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator): proxy mean IoU 0.86, gate failed on side length (+8.4 %): use `fit_side_length`. The rest
pose must be SYMMETRIC (artist 2026-10-17: "our characters should be symmetrical head turns are done through posing"):
use the finish config's `repose` (improve/crystal_canine_finish.json: symmetric legs with plant, head heading -y with
distribute, tail on the midline via to_points along the SHEET's side-view tail curve at x = 0, a rigid face box) -
never geometry symmetrise. Rig a tail chain so the tail can be posed to the side later. Sibling: improve/
drakescale_beast_finish.json (the artist said its HEAD "looks off" - compare the cub's head closely against the
sheet's head detail panel and keep it as generated unless clearly wrong; report). Tone: warm brown scales with copper
tips and a cream belly; check the texture, tint only. The proxy's tail lies flat; the sheet's tail is long, tapered,
curving down to the floor behind - repose it to the sheet's curve rather than rebuilding it (the beast's rebuilt tube
tail read poorly).
UNIT NOTE 2 (orchestrator, artist 2026-10-17 on the sibling beast: "its way too compressed and looks smooshed where we
cant see eyes or any other face details"): check the cub's head the same way - the snout length against the sheet's
side view + head detail panel, a clearly readable orange eye on each side (crisp paint if the proxy's eye is lost), the
cream jaw / mouth line. If the proxy head is crushed, report it with a side close-up next to the sheet's head panel
(the orchestrator decides between a re-generation and a reshape).

==== V1.1 (2026-10-17, orchestrator; WINS over the earlier sections for these two items) ====
(1) TAIL: v1's own tail is ~30 % shorter than the sheet's (chain 3.15 u vs ~4.6 u). Artist rule (review-log "2026-10-17 —
Drakescale Beast v2 tail note"): extend tails by DUPLICATING the generated tail's scale segments, tapering to a POINT,
never a tube. Use the opt-in light.segment_array (in improve/proxy_finish_light.py; working config in
improve/drakescale_beast_finish.json): source = the cub's own tail-root segment, copies along the sheet's side-view
tail curve on x = 0, taper to a point at the sheet's tip; the tail chain follows the copies (centred, posable). Avoid
the beast's issues: keep the spines on TOP (if the copies need a twist about their axis to keep the dorsal side up,
report that the op lacks it rather than editing the kit), and keep the tail visible behind the body in the side view.
(2) EYE: v1's eye reads as a dark almond with a faint orange rim; the sheet's is a soft clear ORANGE eye (head detail
panel). Paint it crisply: find the eye socket surface on each side (the dark almond), paint the iris orange
(~230,140,50) with a darker rim and a small dark pupil + highlight, confined to the socket's own outline; mirror the
design to both eyes (improve/raven_shade_finish.json v3 / improve/iron_scale_knight_finish.json v2b eye paint idioms).
If the socket is layered scale soup and recolour3d bleeds onto scales, report it with a close-up rather than grinding.
Close-ups: head side + front (eyes), tail side + back 3/4 + top.
