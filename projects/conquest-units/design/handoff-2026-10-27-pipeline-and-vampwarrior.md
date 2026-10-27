# Handoff: pipeline overhaul + Vampire Warrior, 2026-10-27 (read this first after a refresh)

This covers sessions 2026-10-25..27; the main session is 4f1944ab. Artist notes are verbatim in design/review-log.md
(entries 2026-10-24..27).

The pipeline review is design/plan-review-2026-10-26.md:
- the goal, the plan, two independent reviews and the merged order;
- at the bottom, "Bake-off result + approved rules (2026-10-27)" = THE CURRENT RULES.

## Goal (artist-confirmed)

1. Characters that resemble the artist's sheets at top stylized-studio quality.
2. Built once as a TOP-QUALITY master (showcase-grade), with methods to lower quality for any target and to keep top
   quality where wanted.
3. Every version riggable + animatable.
4. The highest-quality option first, always.
5. Budgets are guidance; quality wins, and the artist decides exceptions.
6. No cel shading / outlines.
7. Fix "clumpy, like poorly sculpted" at its cause.

## Approved rules (2026-10-27)

1. Heads / faces NEVER go through the voxel remesh. A head is its own part: decimated directly (look-only) or
   template-topology (animatable). Per-part bake with a small cage; smooth shading + a normal map.
2. The body stays on the remesh for now (body no-remesh = a later test).
3. Masters may be smooth-shaded. Each unit declares a shading mode; the contract checks every mesh and visible holes.
4. Budgets are guidance.
5. Checks:
   - per-region drift to the source;
   - eye opening >= 95 % of the source;
   - a range-of-motion report;
   - a Codex review before the artist sees anything.
6. Test builds capped (~3) and batched; use the fast flags (`--retexture`, `--geo-cache`, `--preview-bake`).

## Repo state

- master = b151e0f, pushed to origin + backup:
  - b30d607: all the Duskmaw / Vampire Warrior / kit / research / review work;
  - b151e0f: the kit fast-iteration flags (cherry-picked from branch claude/interesting-bartik-f8c705, which is now
    merged in content).
- Uncommitted after b151e0f: design/review-log.md, design/plan-review-2026-10-26.md, this handoff,
  improve/vampwarrior_STATE.md header. Commit + push them on resume, with the artist's OK.
- Regression pins (no-flag builds must reproduce): vulture_creature 5b3aaa03830de189, drakescale_beast
  64758c5e225e4134, sand_drill_worm 879c02377d7e7a44.

## In flight when context was refreshed

1. **WRAPPED UP at b72dfeb on branch claude/kit-head-path-774826** (local, NOT pushed / merged). The full per-item
   status is on the branch: projects/conquest-units/design/kit-head-path-STATUS.md. Evidence:
   renders/vampwarrior/kit_head_path/.
   - Items 2-7: DONE. The 3 pins verified after 4c56678 and a5ec386.
   - Item 1: PARTIAL. The head part works (head7 drift p50 0.00027, neck blend weights, contract v2 passes). Two bugs
     are fixed in code but NOT rebuilt: (a) the mirrored-half winding (rendered black), (b) light.uv dropping the head
     uv_density (uv_health fail).
   - The 3 regression pins are NOT re-verified after those 2 last changes (they act only in mode "mesh").
   - Found: head7 is a thin double wall (half its faces are an inner surface): add an inner-wall drop or raise
     head_tris.
   - TO FINISH:
     1. re-run the 3 pins;
     2. one H rebuild (overlay_H.json, --retexture --solid): head renders both sides, uv_health passes, coverage
        recovers;
     3. measure the eye opening (>= 95 %);
     4. Codex review;
     5. ask the artist about the merge.
   Original brief: session "Forge kit: bake, shading, contract, rig modes" (local_84d1d55d-d67a-4518-bc18-b19300e79d4e,
   own worktree). Kit items, all opt-in:
   - 1: `head_graft.mode: "mesh"` (the head as its own decimated part, or a pre-built head part);
   - 2: a per-part bake cage;
   - 3: a smooth-reference normal bake (no smooth_texels neutralising);
   - 4: export tangents + AO kept separate;
   - 5: contract (check every mesh, a declared shading mode, a visible-hole check, per-LOD budgets);
   - 6: rig part weight modes rigid / chain / blend;
   - 7: a range-of-motion report.
   Gates: the 3 regression pins. When it reports, review the diff + evidence, then ask the artist about merging.
2. **Hybrid head asset: CHECKPOINTED (stopped cleanly at wrap-up; committed with this handoff).**
   design/reference/vampwarrior/head_graft/head7/hybrid/STATUS.md is enough to continue from alone.
   - Contents: hybrid_head.glb (in the same frame as head7_s56_edit.glb; MPFB hm08 topology; separate eyeballs on
     eye bones; fangs, teeth, tongue, mouth interior; bones head / jaw / eye.L / eye.R; shape keys blink_L / R,
     jaw_open, viseme_A / O / M, brow_raise), rigged / unrigged blends, textures, hybrid_sheet.png, and all scripts
     (scripts/chain.sh work 0.0065 rebuilds everything).
   - Numbers (head ~0.97 glb units tall):
     - face drift p50 / p99 0.0033 / 0.031, ~10x (b)'s;
     - eye opening 99-108 % of the source;
     - lip line open 94 %;
     - 14.3k tris (head 8.7k).
   - Open defects:
     - grey / orange lid-crease bake patches;
     - jaw_open corner stretch up to 20x with a box-shaped opening;
     - viseme_M lips pass through each other;
     - rough ears;
     - over-dense eyeballs.
   - Next: fix the lid crease, run the unrun LOD0 (scripts/hyb_lod0.py), jaw correctives, ear landmarks, slim the
     eyeballs, final renders, Codex review, then test via head_graft.mode "mesh".
   - Eyes can be opened toward the sheet with one parameter: chain.sh work 0.009.
   - Bake-off evidence + scripts preserved in design/reference/vampwarrior/head_graft/head7/bakeoff/ (sheet,
     step-1 grids, metrics, py/ scripts, Codex review).

## Next steps (in order)

1. Review the kit-head-path result: diff, regression pins, evidence. The artist decides the merge.
2. Review the hybrid head: numbers + sheet + a Codex review against design/reference/vampwarrior/head_graft/head_panel.png.
   - Note (Codex): even the source head7 has narrower, more hooded eyes than the sheet. Likeness to the sheet is a
     separate milestone.
3. Vampire Warrior face rebuild: the hybrid head via head_graft.mode "mesh" on the v7 body
   (proxy_v6/vamp_body6_s11.glb), bald. Then a Codex review, then the artist's FACE APPROVAL.
4. The hair pass:
   - extract improve/wren_s5_hair.py (+ elias / varden / lyra) into a module (hairkit.py: scalp mesh, hairline,
     eye landmarks, head BVH, a part sink with chain weights), ~1 day;
   - build the locks from the hair target design/reference/vampwarrior/head_graft/head7_B_render/hair_*.png;
   - spring-bone chains.
5. Body: later, a no-remesh test; LOD / export profiles; the sword (full length, posed in the clips: keep v7.1's
   solution).

## Open decisions for the artist (ask; do not assume)

- **Proposed speed-ups (not yet approved):**
  - (a) the twin digest build OFF by default for unit builds, ON for kit / regression runs (roughly halves CPU + RAM
    per run);
  - (b) an opt-in GPU (OptiX) bake for test builds / `--preview-bake` only; CPU stays for digest runs (the bakes are
    pinned to CPU today: `scene.cycles.device = "CPU"` in proxy_finish_light.py and proxy_finish_build.py).
- **Game branches** (committed, NOT pushed / merged), with the true form as a form shift on the ultimate:
  - Conquest claude/duskmaw-forge-import-6a7761 @ 6037c30 (Voidstep teleport half, 2 turns);
  - Dungeon Diver claude/duskmaw-forge-import-056353 @ dd2a8a8 + 49cd2bd (Duskfall R, 10 s); its multiplayer owner
    must hear about rpc_hero_shift.
- Movement + in-game height for Duskmaw (D1 / D2) and Vampire Warrior: still unanswered; the clips are placeholders.
  The true-form size is a placeholder (2.5x base).
- Duskmaw: v6.2 stands ("fine for now"). The held v6.3 follow-ups (graft fit into the slim torso, sharper red-based
  teeth, darker throat) are in scratch duskmaw_lane2\v63_wip\. Forge follow-ups from the imports:
  - the true form's teeth don't glow;
  - the cyclic clips repeat their first frame (a loop hitch);
  - the true-form bind pose is a tilted mass.

## Unit status

| unit | state | key files |
|---|---|---|
| duskmaw (base) | v6.2 shipped, DIGEST 12e32496b661a1c1 | improve/duskmaw_finish.json, duskmaw_STATE.md |
| duskmaw_true_form | v1.1, no artist model review; imported as the ult form | improve/duskmaw_true_form_* |
| vampwarrior | repo = face-pass-2 review build G4; v7.1 restorable; heading to the hybrid head + hair | improve/vampwarrior_STATE.md (header), vampwarrior_finish.json |

## Key assets / references (vampwarrior)

- **Body proxy:** design/reference/vampwarrior/proxy_v6/vamp_body6_s11.glb, from the Codex turnaround
  codex_body_v6/ (flat boots, no sword).
- **head7 refs** (stylized 3D-render style, bald, POINTED ears = the artist's choice): head_graft/head7_B_render/
  (+ v2/ = refined refs).
- **head7 meshes:** head_graft/head7/:
  - head7_s56_pixal3d.glb (sha 31372f31cc5c);
  - head7_s56_edit.glb (d2886e4a6aa0, the best look so far: lash plates pushed back, chin softened, repainted);
  - head7v2_s11 (the best of the v2 regen).
- **Hair target:** head_graft/head7_B_render/hair_front / side_left / back.png.
- **Rejected:** head5 (double face), head6 (a face patch between hair locks), the v6.1 / v7 / v7.1 hair (fused ropes).

## How to run things

- Python: service\.venv\Scripts\python.exe -P (-P drops cwd from sys.path).
- Blender: "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background.
- **Heavy-job lock** (ONE full Blender build at a time on this 32 GB machine; the kit session uses this exact path):
  "C:\...\service\.venv\Scripts\python.exe" -P "C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\4f1944ab-5e47-4e4d-a91a-e84f71edc340\scratchpad\heavy_lock.py" -- <cmd>
  A stale heavy.lock beside it with a dead pid is taken over automatically.
- **Runners:** powershell -NoProfile -File improve/<unit>_run.ps1. Use FORWARD slashes from bash; a backslash got
  eaten once.
- **Raw-look renders:** improve/rawlook_views.py (front / 3/4 / side) and improve/rawlook_closeups.py (full length +
  head close-ups): blender --background --python <script> -- <glb> <out_prefix>.
- **Heavy-job lock, repo copy:** improve/heavy_lock.py (lock file improve/heavy.lock, gitignored). Use it after the
  kit-head-path session is done; until then use the scratch path above so both share one lock.
- **Pixal3D multi-view:** see head_graft/head7_B_render/gen7.py (Pixal3DBackend, shared_frame, 4096 texture,
  views front / left / right(mirror) / back, seeds 56 + 11). ComfyUI must be up (localhost:8188).
  - Align the side view to the front on the pupil / mouth / chin rows first (polyfit), and mirror left -> right.
- **Codex:** C:\Users\kenne\AppData\Roaming\npm\codex.cmd exec --skip-git-repo-check -C <dir> -s workspace-write
  -i <img>... -o last.txt - < prompt.txt
  - Use -s read-only for code reviews; it then cannot write files, so read its stdout.
  - Its image tool refuses "teeth / maw / devour" wording.
- **Research:** design/research/league-modeling-2026-10-26.md, close-camera-marvel-rivals-2026-10-26.md,
  hair-face-best-practices.md.

## Working rules (from memory; the artist's standing preferences)

- Codex-review every face / result before the artist sees it.
- Face first (bald), then hair as separate pieces.
- Cap test builds (~3), batched.
- Tell the artist about review issues BEFORE any rebuild.
- The highest-quality option first.
- Every character ships in every game: the top master first, lowered as needed.
- Short reports: results, decisions, blockers.
- Never use other studios' models / art as inputs.
- Commit + push (origin + backup) only with the artist's OK.
