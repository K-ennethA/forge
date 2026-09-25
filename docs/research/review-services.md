# Review: forge service layer (read-only), 2026-09-25

Scope: `service/`, `meshgen/`, `mcp/forge_mcp/` (server.py 94 `@app.tool`s, pipeline.py,
task_config.py), `benchmark/`, `projects/werewolf/export/game-drop/`, with the conquest
improve scripts as the duplication comparator. Reviewed against `docs/lane-conventions.md`.
Nothing was changed. Every number below was measured on the working tree at HEAD `9c41725`
(plus the uncommitted conquest files); claims I could not measure carry **ASSUMPTION**.

Measurement scripts (scratch, not committed): a param-lag extractor (AST over the addon's
`@command` handlers vs each MCP tool's signature and body keys) and a duplication script
(normalized-line overlap + `difflib` ratios). Both are heuristic lower bounds; alias keys
(`object`/`name`/`project` used as synonyms) are excluded from the lists.

---

## 1. Ranked findings

### F1 (HIGH, correctness): the seed and the ensemble are unreachable from every caller, and the drawn seed is never reported
- `mcp/forge_mcp/server.py:3233-3241`: `generate_3d` takes image paths, backend, wait, project.
  No `seed`, `texture_seed`, `ensemble`, `hard_surface` or other options. `server.py:3310-3320`
  only ever builds `options = {"views": ...}`.
- `mcp/forge_mcp/meshgen_client.py:158-169` wraps `/generate3d` only; `/generate_ensemble` and
  `/capabilities` (meshgen/service.py:7-10) have no client.
- `addon/forge/tools/model.py:516-519`: the artist's Model-box button sends `{image_path, backend}` only.
- `grep -i "seed|ensemble" mcp/` returns zero hits in forge_mcp, so `fmt_generate_report` never
  shows `seed.value` / `seed.source: "drawn"` from the job (meshgen/jobs.py:81-115 carries it).
- Failure: meshgen now draws a fresh seed per job (base.py:114-131), so a result the artist likes
  can't be reproduced or re-textured (`texture_seed`) through the agent. The ensemble tiers
  merged in 98eb051 are unreachable. docs/research/ear-pipeline-improvement.md step 0.4 asked
  for this, and it is still open.
- Fix: pass `seed`, `texture_seed`, `ensemble`, `hard_surface` and an `options` escape hatch through
  `generate_3d`; add a `generate_candidates` tool over `/generate_ensemble`; print seed value + source
  in every report; do the same for the Model-box operator.

### F2 (HIGH, correctness): generations without a project silently overwrite the previous mesh, and the docstring says otherwise
- `meshgen/service.py:345`: the default output is `image.with_name(stem + "_<backend>.glb")`, which
  sits **next to the input picture**. `meshgen/comfyui_client.py:554` `shutil.copy2`s over it with no
  existence check.
- `server.py:3281` says "the default stays the meshgen output folder". That is wrong: the file lands
  in the image's folder (often `projects/<x>/design/refs/`, **ASSUMPTION** about usual input location).
  system_prompt.md:659 repeats "the service's scratch folder".
- Only the `project=` path is collision-safe (`util.py:3942-3961`). The Model-box button never sends
  `output` (model.py:516), so every run from the button replaces the last one.
- Failure: with drawn seeds, N regenerations of one picture leave 1 file on disk. N-1 GPU runs
  (82-304 s each, measured in base.py:137-140) are lost with no message.
- Fix: in meshgen `_prepare`, never overwrite: use the `-2` suffix rule, or refuse on an existing
  `output`. Correct both docstrings.

### F3 (HIGH, pipeline): "passed" does not check the gate, and a third of the gate names can't be recorded
- `pipeline.py:1081-1090` refuses `passed` only when **no** number exists. Uncovered gate names are a
  note (`:1123-1124`), and a test pins this as a note (mcp/tests/test_pipeline.py:582).
- Werewolf plan (measured): 5 of 10 stages are `passed` with **0** of their own gate names recorded
  (verify_mesh 0/3, rig 0/3, skin 0/2, correctives 0/1, animate 0/1). verify_mesh was recorded `passed`
  while its numbers read `"verify_design": "needs_attention ..."` (build-plan.json:561).
- 7 of the 30 character gate names are emitted by **no** tool: `foot_slide_mm`, `symmetry_residual_mm`,
  `poly_budget_triangles`, `glb_written`, `animated_nodes`, `mesh_exists`, `references_on_disk`.
  `asymmetry_mm` exists only in rigforge_landmarks, not rig_check. Recording them requires
  hand-naming numbers, so coverage fails by construction.
- Fix: gate names must equal real result keys (e.g. `animation_check.gate`, `verify_design.axes.poly_budget.status`);
  `passed` requires every gate name present with a non-fail verdict, or a signed waiver like `override`.

### F4 (HIGH, pipeline): gate lists freeze on disk and drift from the template
- `pipeline.py:630-644` materializes the template only when a plan has **no** stages; an existing plan
  never picks up new gates.
- Werewolf plan vs template (measured): 9 template gates absent: rig 4 (`bend_direction`, `hand_containment`,
  `foot_height`, `ik_reach_headroom_rest`), correctives 1 (`corrective_driver_domain`), animate 4
  (`bone_stretch_budget`, `ik_reach_headroom`, `loop_seam_closure`, `anticipation_reads`).
- Fix: at `load`, union template gates into existing stages additively and add a history note. Never
  drop gates the plan already has.

### F5 (HIGH, pipeline/contract): the stage gates lag the gates that now exist
- Motion-quality tier: `rigcheck.py:3726-3729` (`motion_quality`, `motion_quality_gate`) is missing from
  the animate gate (pipeline.py:198-200). It can't be requested from MCP `animation_check`
  (server.py:4867 signature has no `motion_quality`) or from the bridge (0 hits). This is the gate the
  artist's "running is clearly broken" verdict created (review-log.md, 2026-09-24).
- Region purity: asked for in review-log.md:15. Zero code implements it anywhere (repo grep).
- Silhouette: only on `verify_mesh`. The `generate` stage (pipeline.py:150-156) gates only existence +
  watertight, though `verify_design(reference_image=)` exists.
- Export (pipeline.py:202-208) doesn't gate the drop-in contract checker (`verify_game_glb.py`) or the
  declared quality tier/budget (lane-conventions.md:138-152).
- The animate `tools` list (:201) omits `rigforge_walk`/`punch`/`jump`/`retarget`/`animation_check`.
- Fix: one lane that adds these gates as real keys (depends on F3's naming rule).

### F6 (HIGH, contract drift): the task-config "settle at read" law has no production consumer, and the tier knob doesn't exist
- `task_config.setting()` / `settled()` (task_config.py:522-569) and `pipeline.blocked()` /
  `stage_status()` (pipeline.py:679-703): **zero** non-test callers (repo grep). Only
  `plan_defaults` has one caller (server.py:5994, floorplan).
- So `symmetry`, `poly_budget_desktop`, `rig`, `correctives` and `target_engine` are written and echoed but
  never read. `verify_design` defaults to `PLATFORM_TARGETS["desktop"]=15000` (verify.py:1602,
  rigforge.py:79) whatever the sheet says.
- lane-conventions.md:147-149 says "the tier is declared in the asset's task config and the delivery
  is gated against the declared budget". There is no tier setting in `_character_template`
  (task_config.py:101-158).
- Fix: add a `quality_tier` knob, then have retopo/verify/export read `setting()` at run time and name
  the value used in their report.

### F7 (HIGH, correctness): the `save_project_blend` default chain allows a phantom save
- `server.py:3123-3125`: a blank `project` is dropped, and the addon then guesses the panel's project
  (`addon/forge/tools/projects.py:182-184`). `create` defaults to **true** (`projects.py:225`), so a
  guessed or mistyped name creates a new folder and writes a .blend there.
- The result doesn't say the project was guessed rather than named. MCP exposes no `create`.
- Tests pin both behaviours: `mcp/tests/test_projects_blend.py:105` (no project → sends none) and `:121`
  (never sends create).
- Fix: the addon returns `project_source: "named"|"panel"` and the formatter shouts on `panel`. MCP sends
  `create:false` unless the caller asks to create a project. Update the two pinning tests.

### F8 (HIGH, contract drift): `rigforge_retarget` MCP lags the rewritten API (4ad3c1e)
- The addon reads 20 keys (rigforge_anim.py:5996-6060). MCP (server.py:4598-4652) sends 6.
- Missing: `root_motion`, `heading`, `legs`, `fps`, `find_loop`, `loop_min_s`, `loop_max_s`,
  `loop_max_residual_deg`, `require_slots`, `foot_lock`, `reach_limit`, `frame_step`, `fk_switch`, `replace`.
- `util.py:1871-1894` `normalize_bone_mapping` **refuses** any string except "auto". The addon accepts the
  presets `cmu`/`mixamo`/`100style` (rigforge_anim.py:6025-6031), and the production drop-in uses them
  (build_protagonist_human.py:109-113). The presets can't be reached through MCP.
- `replace` defaults to true (rigforge_anim.py:6006). The addon reports `replaced` (:7122), but
  `fmt_retarget_report` (util.py:2179-2204) drops it, along with `transfer_check`,
  `rest_alignment_deg`, root speed, loop window and seam residual. Re-running a clip overwrites a
  same-named action and nothing is said.
- Fix: expose the new keys, accept presets, print `replaced` and the fidelity block.

### F9 (MED-HIGH, contract drift): 26 addon commands have no MCP wrapper, and the system prompt names tools that don't exist
- No `send_command` reference in forge_mcp (measured):
  - bosses: `attach_boss`, `detach_boss`, `move_boss`, `list_bosses`
  - seating: `measure_peg`, `measure_socket`, `seat_part`
  - trackforge: `track_points`, `probe`, `list_trackers`, `grab_to`
  - mocap: `rigforge_mocap_clip`, `motion_stats`
  - `mesh_repair_bridges`, `fit_to_silhouette`, `sprite_cutout`
  - `rigforge_autotag`, `rigforge_skin`, `rigforge_landmarks`, `rigforge_ik`, `rigforge_echo_skeleton`, `rigforge_weight_maps`
  - `get_activity`, `playtest_pov`
- The bosses/seating/trackforge set also has 0 mentions in system_prompt.md, bridge.py and
  docs/architecture.md.
- `assistant/system_prompt.md:37,487,489` tell the agent to call `rigforge_ik`; `:481,505` tell it to
  call `rigforge_skin`. Neither is an MCP tool.
- The werewolf plan records the workaround twice: `registry.dispatch("mesh_repair_bridges")` and
  `mesh_diagnose`'s `rig` param "via execute_blender_python - no MCP wrapper exists yet"
  (build-plan.json:573,597).
- Fix: wrap the production-used ones (mocap_clip, mesh_repair_bridges, seating, bosses, trackforge,
  ik, skin). Until then, correct the prompt.

### F10 (MED, benchmark contract): silhouette projection-plane diagnostics did not land
- `benchmark/silhouette.py:828-836` `measure_part` still drops `plane_normal`, which `mesh_outline`
  computes at `:678`.
- No OBB extents are reported, and `geometry.principal_frame`'s `variances` (geometry.py:138) are never
  used, so no warning appears when the thin and mid variances are within 2x. That warning is step 0.3 of
  ear-pipeline-improvement.md:326. silhouette.py was last changed in 54f0f88, which predates the
  research (9f1c334).
- `normalize` (silhouette.py:527-532): when `up` has no component along the long axis (the
  side-projection case), it silently switches to the furthest-extreme rule and nothing records it.
- `quality.py:290-304` surfaces only what `measure_part` returns. The metric is still silent about which
  plane it chose. There is also no in-turn `silhouette_score` command (0 repo hits).
- Fix: return `plane_normal`, OBB extents, variances, a `plane_ambiguous` flag and an `up_fallback`
  flag; add the read-only socket command + MCP tool per the research doc section 4.

### F11 (MED, benchmark correctness): `compare` counts a stale leftover artifact as delivered
- `benchmark/compare.py:80-81,114` use `artifact.exists`.
- `runner.py:204-206` sets `exists=True, fresh=False` for a file left over from an earlier run, and
  grades it as missing.
- Failure: a candidate that wrote nothing but finds an old file is graded 0 like a zero baseline, counts
  as delivered, and a faster wall time prints **SHIP**.
- `_comparability` (compare.py:45-60) also ignores `task_version` and `git_dirty`, so runs against
  different task tolerances compare.
- No stale-artifact case in test_compare.py.
- Fix: use `fresh`, add a `task_version` equality check, add tests.

### F12 (MED, pipeline): cost fields never carry money
- `pipeline.COST_KEYS` (pipeline.py:102): `usd`, `tokens_in`, `tokens_out`, `model`.
- The bridge knows each turn's `cost_usd` (bridge.py:3539-3546) and never passes it (0 `cost=` in
  bridge.py). system_prompt.md never mentions `cost`.
- Werewolf plan: 6 stages carry `cost`, all `{"models": ["sonnet"]}`, with 0 usd and 0 tokens.
- The model can't know its own spend mid-turn, so only the bridge can supply it, after the turn.
- Fix (design needed): after a turn that called `pipeline_record`/`advance` on stage X, the bridge calls
  a `pipeline` API to fold that turn's cost in. pipeline.py stays the only writer.

### F13 (MED, latent silent fallback of the seed->56 class): the graph builder skips missing nodes silently
- `meshgen/backends/comfyui_base.py:373` (options) and `:399` (stage seeds) write only
  `if node_id in graph`.
- `load_workflow` requires only nodes 122/316/322 (`:288`).
- Failure: a template refresh that renumbers a KSampler builds a graph at the template's own seeds
  (56/42/42/43) with no error. That is exactly the old bug, re-armed.
- Measured today: all 15 option-target nodes exist in both `image_to_3d.json` (55 nodes) and
  `multiview_to_3d.json` (70). No test deletes a node.
- Fix: add every `OPTION_SPEC` and `SEED_STAGES` node id to `required`, plus a test.

### F14 (MED): meshgen accepts malformed options at submit and fails minutes later
- `meshgen/service.py:300-346` validates `views` and `ensemble` before queueing, per its own rule
  "a malformed request should cost nothing" (`:318-319`).
- Unknown or out-of-range options are refused only in `finish_graph` (comfyui_base.py:375). That runs after
  `ensure_running` (`:511`), a ComfyUI cold start. The test for the refusal
  (test_service.py:572) calls `build_graph` directly, not the submit route.
- Fix: dry-run `resolved_options` + `_check_range` in `_prepare` → 400.

### F15 (MED, units): several unit assumptions are never checked
- `addon/forge/tools/common.py:33` fixes `M_TO_MM = 1000`. `scene.unit_settings.scale_length` is only
  reported (`:464`), never applied. So check_model, verify, diagnose, seating, trackforge and the
  benchmark's `.blend` path (quality.py:63) read a mm-scaled scene 1000x off. **ASSUMPTION:** how often
  artists open `scale_length=0.001` files is unmeasured.
- `service/mesh_input.py:338`: an unrecognised 3MF unit falls back to 1.0 (mm). Its own comment at
  `:330-332` describes this trap.
- STL/OBJ are assumed to be mm with no magnitude check. `benchmark/quality.py:64` maps `.obj` → 1.0, but
  Blender's OBJ exporter writes metres.
- `verify_game_glb.py:225-228`: `--expect-height` is printed and never PASS/FAIL, so a scale or unit error
  in a drop-in passes the contract checker (glTF is metres; the contract humanoid is 1.75 m).
- Fix: gate `expect_height` with a tolerance; refuse unknown 3MF units; warn when `scale_length != 1`.

### F16 (MED, units): the poly budget means triangles in one place and faces in another
- task_config.py:129-135: `poly_budget_desktop`, unit "triangles".
- verify.py:1635-1651 compares `mesh_diagnose.face_count` (polygons) and calls them faces. A quad mesh at
  "budget" is about 2x the triangles. Over budget is `attention` (`:1639`), never `fail`.
- conquest_contract_check.py:71 counts triangles. Two definitions in one repo.
- Fix: count triangles (`loop_total - 2`) everywhere and name the unit.

### F17 (MED): `rigforge_mocap_clip` returns success even when the clip is not ready
- `rigforge_mocap.py:886`: `strict` defaults to false, so the call returns `success` with
  `contract.ready: false`.
- `build_protagonist_human.py:280` calls it without `strict` and never reads `_c["ready"]` or
  `_c["failed"]`. Its own `gate()` re-derives slide, seam and quality later (`:324-382`), so today's build
  is covered. Any other caller (a future MCP wrapper) gets a success-shaped refusal.
- `replace` defaults to true (`:905`).
- Fix: `strict` defaults to true for production callers, or the MCP wrapper refuses on
  `ready: false`.

### F18 (LOW-MED): three geometry fallbacks drop features without recording it
- `service/forge_lib.py:209-210`: the peg chamfer fails → `lead = 0`, while the attached spec
  (`:225`) still claims the chamfer.
- `forge_lib.py:275-276`: the socket mouth chamfer is dropped (`pass`).
- `service/joints.py:667-668`: the pin chamfer is replaced by a plain cylinder.
- Five sibling sites record the same kind of fallback in `plan["clamped"]` (forge_lib.py:1459-1461,
  1957-1960, 3190-3194; maker_lib.py:1762-1767, 1785-1790, 2036-2039). These three don't.
- Fix: append a `clamped` note and set the spec's chamfer to the achieved value.

### F19 (LOW-MED): the task_config floorplan fallback doesn't disclose itself as documented
- task_config.py:226-228 promises "the sheet says which it used".
- `_plan_defaults` (`:343-347`) and `_plan_anchors` (`:350-355`) swallow any exception and return the
  written-down copy with no marker in the sheet.
- Fix: add `"source": "service"|"fallback"` to the sheet history.

### F20 (LOW): game-drop staleness and private coupling
- Stale names:
  - `look_protagonist_human.py:3` names a non-existent `build_werewolf_escaped.py`.
  - `look_protagonist_human.py:234` ships material `werewolf_escaped_look` inside protagonist_human.glb.
    **ASSUMPTION:** renaming it may churn a Godot-extracted material; check before changing.
  - `verify_game_glb.py:18` references a non-existent `werewolf_escaped.images.json`.
- Dead code: `clips_protagonist_human.py:1-6` still describes run-loop/sprint-loop authoring, and
  `author_gait` (`:172`, ~116 lines) has no caller since the mocap switch.
- Private coupling:
  - build and clips both reach into 5 private rigforge_anim functions (`_key_transform`, `_rest_world`,
    `_rotate_about`, `_set_world`, `_world_matrix`).
  - build monkeypatches `rr.op_kwargs` to inject `export_anim_slide_to_zero` (build_protagonist_human.py:460-478).

---

## 2. MCP parameter-lag list

Tools whose parameters lag their addon command, as of HEAD. Addon keys come from the handler
body (heuristic, lower bound).

| MCP tool (server.py line) | addon command | addon keys MCP cannot send |
|---|---|---|
| generate_3d (3233) | meshgen /generate3d | seed, texture_seed, ensemble{structure_n,best_of}, hard_surface, steps, cfg, texture_resolution, remesh_resolution, target_face_count, uv_padding, shape_resolution, smooth_iters, qef, project_back, fix_poles, crease_angle, on_unavailable; output (only via project); no /generate_ensemble, no /capabilities |
| generate_3d (3233) | import_generated / check_model | voxel_size, collection / printer |
| rigforge_retarget (4598) | rigforge_retarget | root_motion, heading, legs, fps, find_loop, loop_min_s, loop_max_s, loop_max_residual_deg, require_slots, foot_lock, reach_limit, frame_step, fk_switch, replace; mapping presets cmu/mixamo/100style **refused** |
| animation_check (4867) | animation_check | motion_quality, mesh |
| mesh_diagnose (1102) | mesh_diagnose | rig (read via `rig_for`, verified at diagnose.py:~1523), apply_modifiers |
| check_my_work (1153) | mesh_diagnose | apply_modifiers, density_ratio, examples |
| save_project_blend (3097) | save_project_blend | create |
| rigforge_export_godot (3959) | rigforge_export_godot | frame_step, lods, unit_scale (+ slide-to-zero, which exists only as the drop-in's monkeypatch) |
| rigforge_generate_rig (3872) | rigforge_generate_rig | band, ik_arms, ik_legs, ik_poles, ik_stretch, max_influences, spring_chains, tag_constrained |
| rigforge_metarig (3759) | rigforge_metarig | breast_bones, echo, echo_dir, echo_path, echo_resolution, method, rigbridge, spring_chains, tag_radius_factor, tags |
| rigforge_retopo (3618) | rigforge_retopo | angle_limit, bake_path, lod_budgets, margin, protect_seams, seams_from_tags, unwrap, voxel_size |
| rig_check (4027) | rig_check | intersection_face_limit, max_weight_maps, render_weights, weight_floor, weight_map_bones |
| rigforge_walk (4717) | rigforge_walk | arm_phase_deg, hip_lower, max_hip_lower, poles, strike_lead |
| rigforge_jump (5305) | rigforge_jump | anticipation_seconds, floor_clamp, hip_setback, load_toe_lift_deg, torso_fold_deg |
| rigforge_punch (5063) | rigforge_punch | guard_forward |
| rigforge_keyframe (4531) | rigforge_keyframe | fk_switch, loop |
| rigforge_action (4457) | rigforge_action | new_name |
| rigforge_cloth (4339) | rigforge_cloth | self_collision, subdivide |
| rigforge_correctives (4177) | rigforge_correctives | direction |
| rigforge_weights (3915) | rigforge_weights | band, rig |
| rigforge_manifest (3555) | rigforge_manifest | create_missing_tags |
| rigforge_untag (3525) | rigforge_untag | include_shared |
| rigforge_auto_uv (3709) | rigforge_auto_uv | method |
| remesh (501) | remesh | adaptivity, preserve_boundary, preserve_sharp, seed, use_symmetry |
| mirror (477) | mirror | bisect, flip, merge_threshold, mirror_object |
| export_stl (735) | export_stl | scale (the metres→mm factor), ascii, apply_modifiers |
| boolean (625) | boolean | solver |
| decimate (532) | decimate | triangulate |
| symmetrize (457) | symmetrize | threshold |
| set_origin (605) | set_origin | center |
| delete_object (725) | delete_object | purge_data |
| rename_object (712) | rename_object | rename_data |
| select_object (703) | select_object | extend |
| load_reference (765) | load_reference | collection, offset_mm |
| turntable (1319) | turntable | dir, keep_frames |
| sculpt_brush (1040) | sculpt_brush | enter_mode |
| execute_blender_python (429) | execute_python | reset |
| floorplan_reconcile (6095) | reconcile_floorplan | plan |
| partforge_open_in_panel (1945) | partforge_open | keep_values, params |

That is 39 tool rows out of 94 tools (38 distinct tools; generate_3d has two rows). There are also 26
addon commands with no MCP tool at all (F9).

---

## 3. Duplication measurement (game-drop vs conquest improve)

Method: normalized code lines (comments, blanks and lines under 12 characters dropped) of A found
verbatim in B; `difflib` ratio for function pairs.

| Pair | Measured |
|---|---|
| render_improved.py lines found in survey.py | 49 / 75 (65.3%) |
| render_improved.py lines found in render_look.py | 23 / 75 (30.7%) |
| survey.py lines found in render_look.py | 31 / 217 (14.3%) |
| Shared lighting/camera constants (world colour + strength, key/fill/rim energies + angles, floor, lens, view transform) | 9 / 9 in all three files |
| `light()` render_look vs render_improved | ratio 0.94 |
| improve_unit.py lines found in look_protagonist_human.py | 4 / 533 (0.8%) |
| look_protagonist_human.py lines found in improve_unit.py | 4 / 210 (1.9%) |
| sRGB→linear: `improve_unit.srgb` vs `look.srgb_to_linear` | ratio 0.73 (2 implementations; a third direction in `look.to_srgb255`) |
| Vertex-colour material build (Principled + VertexColor node) | same 4 lines in both |
| `rigkit.assign_action` vs `rigforge_rig.assign_action` | ratio 0.71 |
| `rigkit.action_fcurves` vs `rigforge_rig.action_fcurves` | ratio 0.43 (a third copy exists in `mechanism.py:137`) |
| `rigkit.neighbour_mean` vs `improve_unit.neighbour_mean` | ratio 0.79 |
| build_protagonist_human.py lines found in clips_protagonist_human.py | 32 / 379 (8.4%): the IK/FK convention keying block |

What this means:
- **Render code is written three times.** That is the real duplication.
- **Palette and region code are not written twice as code.** The two colour pipelines are different
  algorithms:
  - werewolf: tag and deform-weight vertex regions, colours sampled from reference pixel boxes.
  - conquest: geometric face bands, **hand-typed RGB literals**.
- The shared pieces are small: the sRGB conversion, the material node setup and the colour-attribute
  write.
- The divergence that matters is policy. The conquest palette is invented, which violates
  sample_palette.py's rule "no colour is invented or hand-adjusted".

What should graduate into the addon:
1. **Look-dev render command** (key/fill/rim, fixed camera aim, stills/turntable/clips, glb or blend
   input). This replaces 3 scripts.
2. **Colour helpers in `common`**: srgb↔linear, `vertex_colour_material(layer, glow_layer=None)`, and a
   per-face/per-point colour-attribute writer.
3. **`sample_palette` as a command**: reference image + named pixel boxes → palette.json with the
   lit-tone rule. It gives conquest a measured palette.
4. **Public rigforge_anim API** for the five `_private` pose helpers, plus an `ik_fk_convention(rig, frames)`
   helper that removes the build/clips duplicate.
5. **`rigforge_export_godot` `slide_to_zero` param**, replacing the op_kwargs monkeypatch.
6. **Game-contract checker command with per-game profiles.** verify_game_glb (werewolf/Godot glb) and
   conquest_contract_check (Conquest blend) are two contract checkers with 0.9% line overlap. Merge the
   shared checks (height/footprint, feet at origin, facing, triangle budget, colour attribute) and keep the
   per-engine ones (godot_name, image inventory). Neither is tested today (0 references outside their
   folders).
7. **rigkit**: import `rigforge_rig.action_fcurves` / `assign_action` instead of copying them.

---

## 4. Test blind spots

- **meshgen**:
  - Refusal coverage is strong: ensemble and multiview carry 20+ `refused` tests.
  - Gaps: no test deletes a template node (F13), and no submit-route test for unknown/out-of-range options (F14).
  - `wsclient.py` is referenced by 0 test files.
- **mcp**:
  - No test for seed/ensemble passthrough, because the feature is absent (F1).
  - test_projects_blend.py:105,121 **pin** the phantom-save defaults (F7).
  - test_pipeline.py:582 pins "uncovered gate is only a note" (F3).
  - No test compares gate names against emitted keys.
- **service**: worker.py (989 lines), segmenting.py (544), wiring.py (650), export.py (256) and
  electronics_motors.py are imported by 0 test files. **ASSUMPTION:** some of them are exercised
  indirectly through test_api's HTTP routes; I did not trace this.
- **benchmark**: test_quality.py has 7 tests. There is no plane-choice test (a thin/mid variance swap,
  the ear incident shape) and no stale-artifact compare test (F11).
- **game-drop**: verify_game_glb.py, conquest_contract_check.py and the look/build scripts have 0 test
  references. The production-mode contract checkers are themselves unverified. That contradicts
  "Production mode ... gets the gates and the contract checker" (lane-conventions.md:128-131).

---

## 5. Prioritized fix plan

Follows the orchestration law: at most two lanes in flight, and never two lanes in one file area.
server.py and util.py are one file area, so the MCP lanes run one after another.

**Lanes** (one deliverable each):

| # | Deliverable | Files (allowlist) | Evidence | Parallel-safe with |
|---|---|---|---|---|
| L1 | meshgen reach from MCP + Model box: seed/texture_seed/ensemble/options passthrough, `/generate_ensemble` tool, seed printed (F1) | mcp/forge_mcp/server.py (generate_3d only), meshgen_client.py, util.py (fmt_generate_report), addon model.py (generate_and_wait), mcp/tests/test_meshgen.py | test_meshgen + headless model suite; quote the seed round-trip | L2 |
| L2 | meshgen submit hardening: no-overwrite default output, submit-time option validation, required-node list (F2, F13, F14) | meshgen/service.py, backends/comfyui_base.py, meshgen/tests/* | meshgen suites; a test that deletes node 18 and expects a refusal | L1 |
| L3 | Pipeline gate integrity: gate names = emitted keys, `passed` needs full coverage or a signed waiver, additive gate reconciliation at load (F3, F4) | mcp/forge_mcp/pipeline.py, mcp/tests/test_pipeline*.py | re-read the werewolf plan: report its coverage before/after, **without writing it** | L5 |
| L4 | Retarget/mocap MCP surface: 14 keys, presets, `replaced` + fidelity report, rigforge_mocap_clip wrapper, animation_check `motion_quality` (F8, part of F5/F9/F17) | server.py (retarget/animation_check sections), util.py (mapping + formatter), test_animation_tools.py | test_animation_tools; after L1 (same files) | L5 |
| L5 | Silhouette diagnostics + in-turn scorer: plane_normal/extents/variances/flags in measure_part, `silhouette_score` read-only command + MCP tool (F10) | benchmark/silhouette.py, quality.py, tests; addon verify-side command; MCP tool | test_silhouette with a thin/mid-swap fixture; ear-round-3 numbers reproduced with the plane printed | L3 |
| L6 | save_project_blend hardening (F7) | addon projects.py, server.py (save section), test_projects_blend.py | invert the two pinning tests; headless projects suite | after L4 |
| L7 | Stage gates for motion-quality / silhouette-at-generate / contract checker at export (F5) | pipeline.py templates only | depends on L3's naming rule | — |
| L8 | Task-config consumers + `quality_tier` knob (F6, F16) | task_config.py, retopo/verify/export call sites | **needs an artist decision** on tier names and budgets before dispatch | — |
| L9 | Cost attribution post-turn (F12) | bridge.py (post-turn hook) + a pipeline.py API | **design needed**: how the bridge learns which stage a turn touched | — |
| L10 | Wrappers for seating/bosses/trackforge/ik/skin/mesh_repair_bridges + system-prompt correction (F9) | server.py, util.py, system_prompt.md | one wrapper family per lane if it grows | after L4/L6 |
| L11 | Game-drop graduation (section 3 items 1-5) | addon new module(s), game-drop + conquest scripts switched over | rebuild protagonist_human; glb image inventory + verify_game_glb unchanged; digest of conquest improve output unchanged | — |

**Local-worker chores** (small; gate = the module's real suite plus a line-count/symbol integrity check,
per lane-conventions "Local worker gates"):

- C1 compare.py: `fresh` instead of `exists`, a `task_version` check, a stale-artifact test (F11). Gate: benchmark/tests.
- C2 verify_game_glb.py: `--expect-height` becomes PASS/FAIL with a stated tolerance (F15). Gate: run on the shipped protagonist_human.glb; quote the height.
- C3 mesh_input.py: refuse unknown 3MF units (F15). Gate: service test_mesh_input.
- C4 forge_lib.py / joints.py: record the three chamfer fallbacks in `clamped` (F18). Gate: test_forge_lib + test_ornament.
- C5 task_config.py: fallback source marker (F19). Gate: test_task_config.
- C6 generate_3d / system_prompt.md docstrings: correct the default-output claim after L2 lands (F2).
- C7 game-drop docstrings and dead `author_gait` (F20). Material rename only after checking Godot churn (**ASSUMPTION**).
- C8 rigkit.py: import the addon's `action_fcurves` / `assign_action`.

Suggested order: L1 ∥ L2 → L3 ∥ L5 → L4 → L6 → L7, with the chores filling gaps. L8 and L9 wait
on decisions.
