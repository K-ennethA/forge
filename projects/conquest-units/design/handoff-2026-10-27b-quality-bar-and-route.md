# Handoff: the quality bar, the route decision and the Vampire Warrior state, 2026-10-27 (b), read this first

This supersedes `handoff-2026-10-27-pipeline-and-vampwarrior.md` for direction. Keep that file for the earlier rules,
the head assets and how-to-run details. The session id is 6cf4305c. The session's own timeline is
`design/review-2026-10-27-session-summary.md`, and an independent Codex review of it is
`design/review-2026-10-27-codex-session-review.md`. READ BOTH.

## 1. The goal (artist, 2026-10-27, verbatim where quoted)

- "we want models at the level of league of leggends or marvel rivals".
- "for now the gameready ones are fine, but Id like to do in game animations we these and mini animated shorts as
  well".
- The proposed reading, still to be CONFIRMED by the artist:
  - a Marvel Rivals-level master for Dungeon Diver (close camera);
  - a LoL-level version for Conquest (top-down), derived from that master;
  - the ~50 batch Conquest units stay on the fast light pipeline.

The bar, as proposed to the artist:

| area | bar | Vampire Warrior today (R4) |
|---|---|---|
| silhouette | readable at game distance, true to the concept | OK; the cape is a plain curtain |
| surface forms | crisp sculpted planes / edges, nothing lumpy | lumpy, soft, noisy (generated + remeshed) |
| materials / texture | LoL hand-painted value groups / Rivals painterly stylized materials | projected generator texture, muddy, pink skin |
| face | appealing, close-up proof (NO full facial rig for gameplay; see below) | better, eyes off the sheet |
| topology / deformation | clean loops, twist bones, correctives, no tearing | fused parts, the held arm stretches, auto weights |
| animation | snappy, appealing, secondary motion | placeholder loops |

Artist, 2026-10-27: "dont need full facial rig for gameplay - if thats the hard part can leave it out for now".
Artist: "animation is more movement animations like swinging a sword". Confirmed: "yes animation like attack moves or spells in
the ggames". The animation target is gameplay body movement (locomotion, attack moves / sword swings, spell /
ability casts); facial acting is not a priority. The deformation priorities follow from
that:
- shoulders, elbows, wrists in wide swings;
- a sword held in the hand (a hand / weapon constraint, not the chest-riding workaround);
- the cape / hair reacting to fast motion.

So the gameplay head needs, at most, the eye / jaw bones and blink the hybrid already has. The full facial rig
(expression + lip-sync shapes) is OUT of scope until the shorts are actually started.

Honest gap (told to the artist): the light toolchain (AI image -> Pixal3D -> voxel remesh -> auto bake / rig) cannot
reach this bar by tuning. The art-heavy parts (sculpt-quality forms, painted textures, animation polish) need the
artist's hand or a better generation source. The agent can own the technical work: topology, UV, bake, rig,
export, LODs and the regression checks.

## 2. Open decisions: ASK, do not assume

1. Confirm the bar (Rivals master for Dungeon Diver, LoL-level Conquest version).
2. The route for the art-heavy parts:
   - A: the artist sculpts / paints / polishes, the agent does everything technical;
   - B: the agent researches the best current generation tools and tests one against the bar first;
   - C: B first, then the artist fills the gaps.
3. The motion source for the in-game clips and the shorts: hand-keyed by the agent / outside mocap or AI motion,
   retargeted (check this against the "no other studios' art" rule) / the artist keys the hero moments.
4. The look of the shorts: an in-engine style (fast) or film-like renders (Cycles).
5. Whether the Vampire Warrior is the first hero for the new route.
6. The tri budget: R4 is 71k (the cape part adds about 12k) against 60k. The artist said "yes" to R4. Treat that as
   accepting it for test builds only, not as a final budget.
7. Still carried over from the earlier handoff:
   - the game import branches (Duskmaw, not pushed);
   - movement + in-game height for Duskmaw / Vampire Warrior;
   - the twin-digest-off / GPU bake proposals.

### Estimates and the artist's reaction

The artist disbelieved "1-2 day" estimates. The honest form, in agent hours (the time goes to iteration loops, not
to writing code):

| item | agent hours | risk |
|---|---|---|
| retarget setup (once) | 2-4 h | bone orientation fix pass |
| game clip set per character (8-12 clips, given a motion source) | 4-6 h | foot / weapon / cape cleanup |
| template body wrap on s7 + armour split | 3-5 h | template fit to her proportions |
| deformation proof (shoulder, sword grip, walk, neck) | 2-3 h | may reveal topology fixes |

The artist rejected a "6 h timeboxed spike" as the goal: "thats a method to success". The goal is the LoL / Rivals
bar; present methods as means to it.

## 3. What happened this session (short; details in the session summary and STATUS)

- The kit-head-path work was finished and extended: the head as its own part, the full head replacement, an
  automatic head fit, the head on its own texture set, the cape as its own part, and new checks.
- Builds: H2 -> R1 -> R2 -> R3 -> R4. Each fixed one thing and exposed or caused another. The artist's verdict:
  "our work seems to be getting worse or only improving one area, while regressing in the rest".
- Codex review, root causes:
  - compound rebuilds with no locked baseline (the body proxy changed body6 -> s7 -> s56 while the head join was
    being fixed);
  - the voxel mesh used as the animation mesh;
  - no authored topology / UV / weights stage;
  - an arms-down rest pose;
  - a harness bug that contaminated R2 / R3;
  - the orchestrator's process errors (it showed a known defect, scoped regions out of review, went over the build
    cap).
- R4 vs shipped G4 (Codex):
  - better: the face (eyes, liner, lips, fangs), less speckle noise, the boots;
  - worse: the neck join step, the plain procedural cape, pink thighs, scalp faceting, flat neck planes, lost
    collar / shoulder / torso / shin detail (the s56 body).
- Checks on R4:
  - PASS: part_join, open_edges, uv_health, regress texels;
  - FAIL: clip_stretch (the held sword arm, the elbow folded to 41.9 deg) and rom_neck (138).
- Body / cape stretch: in R3 / R4 the cape part stays under 1.1x stretch in the idle / walk clips (FIXED at its
  cause). The shipped G4 has the cape dragged by the sword arm: 27.7x max, 1869 edges > 2x. The shipped games carry
  that defect.

## 4. New standing rules (memory)

- `no-visible-defects-to-artist`: fix first; never scope regions out of a review; diff vs the shipped build first.
- `lock-stages-one-variable`:
  - stage gates with locked artifacts;
  - one hypothesis / one variable per build;
  - compare vs the sheet + shipped + the last approved;
  - a config digest per stage;
  - 3 failed experiments = stop and diagnose.

## 5. Repo state (NOTHING committed or pushed this session; commit only with the artist's OK)

### Main checkout, master @ 01c1d56 (clean apart from these untracked files)

- `design/reference/vampwarrior/`:
  - `codex_body_v7_bald/` + `proxy_v7_bald/`: redrawn bald body refs + meshes. REJECTED (worse than body6 below the
    chin).
  - `codex_body_v6_bald/` + `proxy_v6_bald/`: body6's refs with only the hair painted out (pixel-identical outside the
    hair mask); seeds 7 / 11 / 56. **s7 = the best body reference** (keeps the cape; the choker is fused to the
    neck).
  - `codex_body_v6_nocape/` + `proxy_v6_nocape/`: the cape also removed (Codex invented the back / legs).
    `cape_mask_{front,side_left,back}.png` + `cape_outlines.json` = the cape silhouettes. s56 = the cape-free body:
    the arms are cleanly separate, but it is less detailed than s7 / body6 (Codex: retire it as a master).
- `design/review-2026-10-27-codex-session-review.md`, `design/review-2026-10-27-session-summary.md`.

### Kit worktree `.claude/worktrees/kit-head-path-774826`, branch claude/kit-head-path-774826 @ b72dfeb

Uncommitted, about 870 lines in 8 tracked files + 3 new scripts. Every key is opt-in.

- Regression pins re-verified after the kit changes: vulture 5b3aaa03830de189, drakescale 64758c5e225e4134, worm
  879c02377d7e7a44. Re-run them after the LAST changes (head_profile pad, drop_inner, the head-tile parts / unwrap /
  AO) before any commit. Those changes are guarded and should not affect them.
- `light.head_graft.replace`:
  - `neck_z`, `margin`, `hull_z`, `profile_pad`, `tuck`, `tuck_in`, `join_tol`, `join_max_area`, `rom_max_neck_si`;
  - `fit` (trimmed-ICP scale / place onto the body's own head; NOTE: it does not converge in 40 iterations and drifts
    slowly in s).
- `light.head_graft.texture` (`res`, `ao` default false, `uv_angle_deg`):
  - the head gets its own material + images in UV tile u 1..2;
  - the vertex-coloured light.parts go in u 2..3;
  - the head tile is moved to 0..1 just for the bake (Cycles bakes only inside 0..1).
- `light.head_graft.drop_inner` (`rays`, `dist`, `cone`): head7's hidden inner wall dropped (58.6k of 175.5k faces).
  CAVEAT: the head low then has 9100 open edges (the contract counts them hidden; inspect them).
- `light.clip_stretch`: every edge's stretch in the shipped clips; contract `clip_stretch`.
- Contract checks: `part_join`, `rom_neck`, per-tile UV overlap (`conquest_uv_tiles`).
- `rig.chains[].radius`; light.parts weight `cloth:<c1>,<c2>..`.
- `vampwarrior_parts.cape_part` + `improve/trace_cape.py`: the procedural A-line cape from the masks + the body
  clearance. Geometry clean, look plain.
- `improve/regress_vs_shipped.py` + `regress_metrics.py`: shipped vs new, the same render script, per-band texel
  density, a Codex whole-model review. Use before any artist showing.
- `palettes/vampwarrior/default.json`: + `cape_outer`, `cape_lining`.
- Docs: `docs/conquest-new-unit-flow.md` (master-quality opt-ins); `design/kit-head-path-STATUS.md` (full build log
  H2..R4).
- Untracked: the s56 / s7 glb copies under `design/reference/vampwarrior/proxy_v6_*` (copies of the main checkout's;
  the builds read them from here) and `renders/vampwarrior/kit_head_path/H2/`.

## 6. Evidence + how to run (session scratch; it may be cleaned by the OS)

Scratch = `C:\Users\kenne\AppData\Local\Temp\claude\C--Users-kenne-OneDrive-Desktop-git-forge\6cf4305c-aefd-45f5-a14b-df8f175d0fec\scratchpad`

- `H2/ R1/ R2/ R3/ R4/`: each build's improved / rigged blends + glb + json, textures, renders `r/`, contract
  `check_rigged.json`.
  - R4 also has `regress/` (the shipped vs new sheet, metrics, codex_regress.txt) and `vampwarrior_finish_R4.json`
    (the full patched config), `intake_R4.json`.
- `prep_R4.py`: builds the R4 config, i.e.
  - the overlay;
  - the joints fitted to s56's stage-1 proxy;
  - the sword-grip clips re-solved with the v7.1 solver at old scratch
    `4f1944ab.../scratchpad/vampwarrior_lane2/clipgen.py` (hold: grip [-1.5, -0.35, 6.5], hand_dir
    [0.6, -0.75, -0.1], palm_u 0.3).
- `run_R4.sh <tag>`: copies the patched CONFIG + INTAKE into the worktree, builds under the heavy lock, copies the
  outputs out, runs the contract + renders, then restores the tracked files.
  - HARNESS RULE: stage 1 runs in a separate process and reads the config FROM DISK. An in-memory overlay
    (`overlay_build.py` with a json) does NOT reach it. That was the R2 / R3 contamination.
- `session_review/`: the Codex review packet (timeline, 20 images, docs, metrics, prompt, review.txt).
- `b6c/intake.json`: s56's sheet registration with the frame pinned to body6 (chest / waist front, y -0.38).
  Without the cape, sheet_proxy's bbox centring moves the body about 0.38 u.
- `body6_solid_main.npz`: a backup of body6's stage-1 solid (`%TEMP%\vampwarrior_solid_main.npz` now holds s56's).
- Heavy-job lock: still the old session path (see the earlier handoff).

## 7. Known defects + deferred items (not fixed)

### Head (head7 / hybrid)

- The grey smears beside / under the eyes and a faint ghost brow line above the right brow are painted into
  head7_s56_edit's OWN texture (visible in the bake-off's source column). Fix them in the source texture, not the
  bake.
- Likeness: head7 has narrower, more hooded eyes and steeper brows than the sheet (Codex). The eye-opening check
  measures fidelity to head7, not to the sheet.
- The R4 fit scale is 1.214 (hand-tuned 1.07, R3 1.154): the head is about 13 % bigger than the sheet-tuned
  placement. Check the proportions before locking.
- The hybrid head's open defects (earlier handoff): lid-crease bake patches, jaw_open corner stretch, viseme_M lips
  intersect, rough ears, over-dense eyeballs. The jaw / viseme items matter less now (no facial rig for gameplay).

### Body / rig (R4)

- The neck join reads as a step; ledges behind the ear; flat neck planes. part_join only proves "no doubled
  surface".
- s56 skin is pink (thighs) vs the pale head; s56 has less armour detail than s7 / body6.
- The held sword arm: the elbow folds to 41.9 deg for the grip at shoulder height. No twist bones, no correctives.
  The sword rides the chest bone, not the hand.
- The procedural cape: clean and independent but plain (no layered torn strips, no texture). One spot sits about
  0.023 u inside the body clearance.
- uv_health passes with about 5.3k body UV islands and p95 area distortion about 10. Investigate before any
  production bake.

### Light-route fixes proposed BEFORE the Codex review (superseded by the route decision; valid only if the artist keeps the light route)

1. Bake the cape part's texture + normal from s7's cape.
2. Transfer s7's surface detail / colour onto the cape-free body (the refs are pixel-identical outside the cape), and
   recolour the skin.
3. A blend band at the neck.
4. Clean the eye smears in head7's texture.

## 8. Estimates given this session (the artist disputed the day-level ones; use agent hours, section 2)

- Production model per hero (old day-level form):
  - first hero about 5-7 days, later heroes about 3-4;
  - minus about 2-3 days now that the facial rig is out of scope.
- Shorts: about 1-2 weeks per 30-60 s short (the animation dominates). Facial rig + cloth / hair sim come only when
  shorts start.
- The proposal to prove the whole chain on Vampire Warrior first (production model -> 3-4 game clips -> a 15 s test
  short) was not answered.

## 9. The orchestrator's view of the Codex plan (told to the artist)

- Agrees with the diagnosis and with stage gates.
- Changes proposed:
  1. Scope the production route to heroes only.
  2. Translate "sculpt / paint weights" into agent-doable steps:
     - template-topology wrap (as with the head hybrid);
     - armour as separate pieces;
     - bake s7's detail.

     True sculpt-level refinement is where the artist's hands beat the agent's.
  3. Get the A-pose by REPOSING s7 with bones, not by regenerating a 4th body.
  4. Prove deformation with bare armour blocks BEFORE investing in armour detail / textures.

## 10. Suggested next step

Get the artist's answers to section 2 (above all the bar + route A / B / C). Whatever the route:

- lock a body reference: s7 is the candidate;
- repose it to an A-pose by bones (no new regeneration);
- build production topology from a template (as with the head hybrid) with a designed neck seam;
- prove deformation before any detail / texture work. Test REAL gameplay poses:
  - attack swings with the sword in the hand;
  - spell casts (arms raised / thrust forward);
  - waist / wrist twist;
  - a run;
  - the cape / hair under fast motion;
  - not just a walk + neck turn;
- every build: one variable, compared vs the shipped build over the whole model before the artist sees it.
