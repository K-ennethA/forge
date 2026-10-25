LANE: raven_shade v1 DRAFT — new from-scratch Conquest unit from the artist's sheet (firefly pattern).

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
STATE: improve/raven_shade_STATE.md. SPEC: design/review-log.md entry "2026-10-16 — NEW UNITS x14: batch of artist reference sheets" (section RAVEN SHADE) (artist
verbatim + binding reading). SHEET: design/reference/raven_shade-character-sheet.png — LOOK AT IT (Read the image) before writing a constant;
silhouette is judged against the image, never a text transcription. MEASUREMENTS: design/reference/raven_shade/
intake.json (views, axes, floor lines, half-width profiles, sampled swatches, ortho_frame, side_facing,
view_checks, palette_fallback; view_masks.png = the exact silhouettes the gate scores against) + its
intake_overlay.png. Measure anything else you need straight off the sheet pixels with PIL (service venv,
python -P) and cite view + px in the constant's comment.
INTAKE NOTES (smart intake; what was auto-chosen and what to check):
  - removed 2 framed panel(s), 84 title blob(s) and 60 rule line(s) before the split (was --region)
  - dark subject on a dark sheet at x 18-443; 443-804; 804-1196 (the threshold lost 38%, 43%, 43% of the filled silhouette): BiRefNet matte merged in, enclosed background carved (393, 0, 406 px)
  - front axis 236.0 px (mirror-symmetry of the body mass); the feet-base rule says 268.5
  - back floor 741 px = the lowest body contact; ignored a part with no leg-like support hanging 33 px lower (tail curl / pool / unpaired tail) (raw lowest row 774)
  - back axis 601.0 px (mirror-symmetry of the body mass); the feet-base rule says 639.5
  - side axis 989.5 px (centre of the floor contact span); the feet-base rule says 942.5
  - front and back body widths disagree (0.80, eroded silhouettes): one of them may be drawn 3/4 or in perspective
  - probable 3/4 / perspective view(s): back (mirror IoU 0.71, tone asymmetry 0.67) -> ortho_views front,side (a 3/4 SIDE is not auto-detected: check it)
  - side view faces image-left (flag, colour score -0.00) -> meshgen view 'left'
  - front-vs-side height: front 6.24 u, side 6.36 u (+1.9%)
3D REFERENCE: intake.json `proxy` block - proxy unreliable, work from the sheet (side length off by +8.7%) (mean IoU 0.85, side length +8.7%, sha 040c9b3e, seed 56); panel: design/reference/raven_shade/proxy/raven_shade_proxy_vs_sheet.png
  When the proxy gate PASSED: take every DEPTH the sheet cannot give (body / head / neck / tail front-to-back,
  cross-section shapes) from intake.json `proxy.sections` + `proxy.landmarks` and cite them in the constant's
  comment with the proxy sha (8 chars). When it did not pass (or there is no proxy): depths come from the side
  view alone - say so in the report. The proxy is a reference only: never import or trace its mesh; it copies the
  sheet's pose (rest pose stays symmetric); where it disagrees with the sheet, the sheet wins.

DELIVERABLE: improve/raven_shade_build.py + improve/raven_shade_parts.py + palettes/raven_shade/default.json (+ any
palettes/raven_shade/<skin>.json the brief names) producing the v1 draft through the scaffolded runner.
DELTA vs firefly: hunched raven biped with a feather cloak to the floor; light flow (proxy as base). The red eye is drawn as a painted eye, not a glow. Keep the beak, eye and crest dense.

FILES (allowlist; everything else read-only — a needed edit elsewhere is STOP-AND-REPORT):
  improve/raven_shade_build.py, improve/raven_shade_parts.py, palettes/raven_shade/*.json, and the runner's own
  outputs (improved/raven_shade*, improved/textures/raven_shade_*, rigged/raven_shade*, renders/raven_shade/*,
  improve/log_raven_shade_*, improved/check_raven_shade.json, rigged/check_raven_shade.json).
  The scaffolded improve/raven_shade_{run.ps1,render.py,clips.py,compose.py,bake_diff.py} are NOT yours: if one
  is genuinely wrong, report the defect + the one-line fix instead of editing it. design/reference/raven_shade/**
  (intake + proxy) is read-only too.

SCRATCH: every temp file (preview blends, logs, test renders) goes under C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\5eaeb76f-be56-497f-8b1e-0a29f5e6168b\scratchpad/raven_shade_lane/ --
other lanes run in parallel in the same scratchpad (2026-10-09: seedant + shadowbun overwrote each other's logs).
LOOP: iterate geometry with `--preview <scratch.blend>` + the render script's ortho_front/ortho_side/front views
on it, comparing against the sheet crops (intake ortho_frame) until the silhouette sits on the sheet; then one
full run: powershell -NoProfile -File improve/raven_shade_run.ps1 (hidden, headless; ~minutes; run it in the
background and wait on it, never windowed). Fix and rerun only on a real failure.

EVIDENCE (quote in the report, numbers not adjectives): the DIGEST line; both checkers' "N checks, M failed"
with every failing check named (cell_fit report-only is expected); total tris vs the tier window + per-part
table; open edges (every open part + reason); retopo low->high p99; bake coverage %; THE NUMERIC SHEET GATE —
every `GATE view=...` line verbatim (per-view IoU, in-frame IoU, row error %, the proxy's IoU beside it), the
`GATE mean_iou=...` line, and `GATE bake_reference=flat`; for every FLAGGED view (more than 0.05 IoU below the
proxy) either the fix or why the sheet wants the build's shape; which proxy sections / landmarks you cited (or
"no proxy" / "proxy unreliable"); your own read of renders/raven_shade/raven_shade_sheet_vs_model.png (where the
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
  1. python -P improve/proxy_finish.py init raven_shade   (writes the stub improve/raven_shade_build.py, improve/raven_shade_finish.json,
     palettes/raven_shade/default.json). Set "mode": "light", tier regular [8000, 22000], body_tris ~16000-20000.
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
  5. Full run: powershell -NoProfile -File improve/raven_shade_run.ps1 (background, wait). Then
     python -P improve/proxy_finish.py gate raven_shade and python -P improve/proxy_finish.py panel raven_shade.
  6. Fill improve/raven_shade_STATE.md in the vulture_creature STATE style (version line with the proxy sha, what the
     finish changed, knobs, open items for the artist).
FILES on the light flow: the allowlist above PLUS improve/raven_shade_finish.json and improve/raven_shade_STATE.md (the stub
build is written by init; do not hand-edit proxy_finish*.py - a kit defect is STOP-AND-REPORT).
EVIDENCE adds: the proxy_finish gate numbers (finished vs sheet AND vs the raw proxy, mirror p99, open edges, tris),
the panel path renders/raven_shade/raven_shade_finish_panel.png and your read of it (face intact? anything melted?).
REPORT: short. Results, numbers, deviations from the sheet, blockers. No narrative.
UNIT NOTE (orchestrator, proxy panel read): the proxy gate failed on side length only (+8.7 %; mean IoU 0.85): use
`fit_side_length`. Both front and back are drawn 3/4 (the back was left out of the generation); the side is the
truest view. Check: (a) the diamond PENDANT clasp (tan 191,168,153) at the throat barely reads on the proxy - if it is
lost, paint it back as a small decal-like recolour3d at the sheet's position (geometry as generated); (b) the sheet's
blade-feather tips are steel-blue (97,102,127) - keep that tip tint; (c) the red eye (141,55,65) must read. The beak /
crest / eye stay as generated.

==== V2 (2026-10-17, artist verdict on the v1 batch; this section WINS over everything above) ====
SPEC: design/review-log.md "2026-10-17 — Batch x15 v1 verdict" (artist verbatim + binding reading). Start from the v1
state: improve/raven_shade_finish.json, improve/raven_shade_STATE.md, renders/raven_shade/raven_shade_finish_panel.png.
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
3. Then the full runner, `proxy_finish.py gate` + `panel`, and update improve/raven_shade_STATE.md to v2.
EVIDENCE adds: a FRONT ortho before/after pair showing the symmetric rest pose (renders/raven_shade/raven_shade_v2_symmetry.png).
REPORT: short. Numbers, deviations, blockers. No narrative.
UNIT V2 NOTE (orchestrator): (a) Artist: 'raven shade also has symmetry issue and should have 2 eyes'. Symmetric rest pose (v1 mirror p99 0.856 u: head turned to -x, legs staggered). After the repose, give it TWO eyes: the generated eye is on the -x side only; create the +x eye as the mirror of the -x eye (its red iris 153,52,67 / sclera / dark surround) painted at the mirrored position on the straightened head - a crisp paint within the eye's own outline (see the eye_rim / eye_iris paint idiom in improve/frost_giant_child_finish.json), geometry only if the socket needs it. (b) Artist: 'can we make its last wing feather red so it looks like a blood soaked blade': on EACH wing / cloak side, the LAST (longest, lowest-hanging tip) blade feather turns red - deep blood red toward its tip (sheet red chip 141,55,65, darker 90,25,32 at the very edge is fine), fading into the steel-blue / charcoal toward its root, like a blade dipped in blood. Paint only (recolour3d on that feather's faces), mirrored on both sides. Close-ups: head front (both eyes) and each red feather.

==== V3 (2026-10-17, artist "the face looks crooked"; this section WINS over V2 and everything above) ====
SPEC: design/review-log.md "2026-10-17 — Raven Shade v2 face note". The head was crooked because the proxy SCULPTED it
from the sheet's 3/4 views. The proxy is RE-GENERATED from symmetric views (design/reference/raven_shade/proxy/, sha
92e56d17, mean IoU 0.79, side length +6.9 % -> use fit_side_length; front IoU 0.74 is the sheet's near-profile front,
expected). Renders: proxy/sources/sym_views.png - straight beak, level eyes, symmetric body, pendant on the chest.
The old proxy is in proxy_v1_3q/ (do not use it).
Rebuild on the NEW proxy (the v2 config's light.yaw / repose were for the old proxy - drop or redo them; the new one
should need little or no repose: measure first). Carry over the artist's v2 asks: (1) TWO red eyes - the new proxy's
eyes read pale / pinkish: paint both irises the sheet red (141,55,65 / 173,53,68) with the dark surround, crisp, level,
mirrored; (2) the LAST (lowest, outermost) blade feather on EACH side blood red toward its tip (same schedule as v2:
90,25,32 edge, 141,55,65 over the bottom ~0.4 u, fading by ~1.2 u); (3) the throat pendant clasp tan (191,168,153)
on the midline, and the back clasp if the new proxy has one (report); (4) blade-tip steel-blue tint as v1/v2.
Close-ups: head front (beak straight, both eyes), side, each red feather. Update improve/raven_shade_STATE.md to v3.
