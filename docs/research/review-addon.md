# Read-only review: forge Blender add-on (addon/forge)

2026-09-25. Read-only: this file is the only change. Reviewed against
`docs/lane-conventions.md` (measured numbers, honest thresholds, defect -> gate).
Every line number below was read in this pass. Anything not established by the
code or a measured comment in it is labelled **ASSUMPTION**, with the
experiment that would settle it.

Scope read in full: `server.py`, `prefs.py`, `tools/registry.py`,
`tools/__init__.py`, `tools/projects.py`, `tools/rigforge_anim.py` (all 7,499
lines), `tools/rigforge_mocap.py` (964). Read in part: `common.py` (1-620),
`rigcheck.py` (resolvers, rig_check rollup, reach/stretch gates, sole
selection, animation_check sampling), `rigforge_rig.py` (ik_limbs,
apply_ik_convention, rig_forward_axis, assign_action, the export loop),
`spriteforge.py` (sprite_cutout), `seating.py` (surface, ledger pegs, stale
guard), `diagnose.py` (self-intersection), `rigforge_landmarks.py`
(facing_vector, except sites), `trackforge.py` (except sites), plus the
consumer scripts `projects/werewolf/export/game-drop/build_protagonist_human.py`
and `clips_protagonist_human.py`.

Sizes (`wc -l`): rigforge_anim 7,499 / rigforge_rig 4,832 / rigforge_landmarks
4,771 / rigcheck 4,653 / rigforge_skin 3,764 / rigforge_mocap 964. 93
registered commands, no duplicate names, every `READ_ONLY_COMMANDS` entry is a
real command.

---

## Ranked findings

### 1. Leftover live pose leaks into authored clips, gates and the export (HIGH)

- **Evidence.** Only `rigforge_retarget` starts its bake from rest
  (`rigforge_anim.py:6864-6872`, `bone.matrix_basis = Matrix.Identity(4)`),
  and that fix was measured: two identical retargets differed by 0.5889 m of
  torso because walk-loop's root travel was still on the live root
  (comment at 6865-6868). Walk (1900-2663), punch (2944-3590), jump
  (3867-5245) and keyframe (1349-1525) never reset the pose. Each keys only
  some controls. Walk, for example, never keys chest, neck, head, shoulder,
  hand_ik or toe_ik, so on every evaluation those channels hold whatever
  `matrix_basis` the previous operation left. The export has the same gap.
  `rigforge_export_godot` bakes action after action onto the deform rig
  (`rigforge_rig.py:4446-4456`: `assign_action` then `bake_action_onto`) with
  no reset between them. So any channel clip N leaves unkeyed carries clip
  N-1's last evaluated frame. `animation_check` and `motion_statistics` take a
  snapshot and restore it (`rigcheck.py:3848, 3919`; `rigforge_mocap.py:678-710`),
  but they sample on the inherited pose rather than from rest.
- **Measured instance.** The production build has a workaround:
  `build_protagonist_human.py:185-188` says walk-loop's frame-1 heel roll
  (left toe 77.9 mm up) leaked into the first idle attempt. It now calls
  `reset_pose()` before every authoring step (188, 279, 307, 317, 448, 454),
  but that fix is in a project script, not in the add-on.
- **Failure scenario.** Some examples:
  - An MCP session runs `rigforge_punch` and then `rigforge_walk`. The walk
    evaluates, is gated and exports with punch's last head/hand_ik basis
    still on it.
  - `rigforge_export_godot` with a different `actions` order bakes different
    curves for the same clip.
  - `animation_check` gives a different verdict depending on what ran before
    it.
- **Unverified.** Whether the current shipped glb carries a non-zero leak is
  an **ASSUMPTION**. It depends on each clip's unkeyed channels and on the
  last frame of the clip before it. Settle it by exporting the build's action
  list in two orders and diffing per-clip curve digests.
- **Same class elsewhere.** `spriteforge.py:1391` calls
  `bpy.data.images.load(path, check_existing=True)`. That reuses a cached
  datablock for the same path, so a re-run on an edited PNG keeps the old
  pixels (**ASSUMPTION** that artists re-run on the same path).
- **Fix direction.** Add one add-on helper, "author from rest": reset
  `matrix_basis` on every pose bone and key rest on the controls the clip
  does not author. Call it from walk, punch, jump, keyframe(`clear`) and from
  each iteration of the export loop. Gate it with an order-swap export digest
  test.

### 2. `repo_root()` can never return "" and silently creates a shadow projects root (HIGH)

- **Evidence.** `prefs.py:39-40` builds a candidate two levels above
  `addon/forge/prefs.py` and returns it `if os.path.isdir(candidate)`. The
  parent of an existing file's parent always exists, so the check always
  passes. The documented zip-install refusal (`projects.py:106-114`,
  docstring 90-93) is therefore unreachable. `projects_dir()`
  (`projects.py:100-103`) returns `<whatever is two levels up>/projects`.
  `save_project_blend` then runs `os.makedirs` there with `create` defaulting
  to true (`projects.py:225, 229-239`).
- **Failure scenario.** An add-on installed or linked anywhere other than the
  repo, for example as an extension under Blender's scripts directory, saves
  projects into `...\scripts\projects\<name>\`. The save succeeds and the
  artist's work lands in a folder that the bridge and MCP
  (`FORGE_PROJECTS_DIR`) never read. This is the shadow-root class.
  `FORGE_PROJECTS_DIR` set for the bridge but not in Blender's environment
  gives the same split (`projects.py:94-99`).
- **Fix direction.** Check for a repo marker such as `run_tests.ps1`,
  `start_forge.ps1` or an existing `projects/` directory instead of
  `isdir`. Have `save_project_blend` report which root it used and where that
  came from. Add a headless test that loads prefs from a copied add-on tree.

### 3. A timed-out socket command still runs later (HIGH)

- **Evidence.** In `server.py:245-264` the client thread stops waiting after
  `job_timeout` (default 600 s, `prefs.py:101`) and replies with an error.
  The job stays in `self._queue`. `drain()` (285-306) later executes every
  queued job and has no cancelled flag.
- **Failure scenario.** Blender is busy (a modal dialog, a long bake), so the
  MCP caller gets "Command timed out" and retries. When Blender frees up,
  both the original and the retry run, for example two
  `rigforge_retarget(replace=true)` calls, or an `execute_python` that
  applies twice. The error said the command did not run, but it did, later.
- **Fix direction.** Set `job.cancelled = True` at timeout, and have `drain`
  skip cancelled jobs and log them. Add a test that queues a job behind a
  blocking one past a short timeout and asserts it never executes.

### 4. A crashed rig_check placement gate rolls up as `pass` with "Placement is clean" (HIGH)

- **Evidence.** In `rigcheck.py:1797-1859` each of the ten placement gates
  is wrapped in `except Exception`, which becomes
  `{"verdict": "unmeasured"}`. The rollup (1875-1881) only reacts to `fail`
  and `attention`. `unmeasured` changes nothing, so `gate` stays `"pass"`.
  Then 1895-1913 prints "Placement is clean: left/right asymmetry None mm,
  ..." because none of the blocks said fail or attention.
- **Failure scenario.** A regression inside `bend_direction` (a TypeError,
  say) turns the gate that caught backwards knees into a green light. The
  sentence is success-shaped and the numbers in it are `None`. No suite
  covers this path: every `unmeasured` hit under `addon/tests/` is a
  legitimately empty input (`headless_animgates.py:423, 446`,
  `headless_jump.py:730, 1431`).
- **Fix direction.** Keep "unmeasured because there is nothing to measure"
  separate from "errored" (`verdict: "error"`), and roll `error` up as at
  least `attention`. Add a negative test that monkeypatches one gate to raise.

### 5. Five reach models, three of them the metric the house rules call dead (MEDIUM-HIGH)

The lane conventions (lines 54-57) say leg reach is measured by pushing the IK
target far, not summed off the pre-bent rest chain (3.5% off), and not as
hip-to-ankle over the rest chain (which saturates once `IK_Stretch = 0`). The
code has:

| # | Where | Model |
|---|---|---|
| a | walk, `rigforge_anim.py:2127-2142` | Measured `max_span` (the conventional one). Rationale at 2117-2126: 528.0 mm delivered vs 510.3 mm summed. |
| b | punch, `_punch_stance` 2886-2940 | `DEFAULT_REACH_MARGIN * leg_length`, where `leg_length` is the rest hip-to-ankle line (1731-1738). No posed-rig correction loop. |
| c | jump, `jump_legs` 3830-3833 plus `_extension_headroom` 3846-3863 | Summed rest chain, capped at 0.98. |
| d | retarget, 6520-6523 | `REACH_CAP` (0.98) times the summed rest chain of the target controls. This is the production mocap path: it drives `hip_lowering` (6547-6568, 6782-6832) and the ankle pull (6592-6598). |
| e | gate, `rigcheck.ik_reach_headroom` 2730-2761 | DEF span over summed rest length. `rigforge_anim.py:2427-2430` records that this ratio sat at exactly 1.0348 for a 279 mm step and a 387 mm step once `IK_Stretch = 0`. |

- **Cost.**
  - Against a 3.5% sum-vs-delivered gap, the 0.98 cap in (d) holds real
    mocap legs about 5% short of the leg's delivered reach (0.98 × 510.3 /
    528.0 = 0.947). That buys extra hip lowering on every production gait.
  - The gate in (e) marks a physically legal full extension (about 1.035 on
    the synthetic rig) as `fail` (over 1.0) and cannot tell "straight" from
    "asked for 30% more".
  - The synthetic-rig numbers are measured. Whether the werewolf has the same
    gap is an **ASSUMPTION**: settle it by running the `max_span` probe on
    `werewolf-form-a_retopo_rig` and comparing it with the rest sum.
- A related convention drift: `locomotion_frame` takes the "hip" as the head
  of the first `fk_chain` bone (1731-1736) rather than the average of the
  `DEF-thigh` heads (convention line 43). Punch's stance solve uses it.
- **Fix direction.** One `leg_reach(rig)` in a shared module: the measured
  probe, cached per rig. Every cap and the gate divide by it. Re-pin 0.98
  with a measured derivation. This is a research-backed lane, not a chore.

### 6. The mocap "contract" ignores the retarget's own landing check, and a not-ready clip still returns success (MEDIUM)

- **Evidence.**
  - `rigforge_mocap.py:920-948` builds `failed` from the slide gate,
    deformation gate, seam and motion quality only. The retarget's
    `transfer_check` (controls that did not land where they were sent,
    `rigforge_anim.py:6976-7007`) is a warning at more than 1.0 deg or
    1.0 mm, a threshold with no derivation quoted. It never reaches
    `contract.ready`.
  - `strict` defaults to false (`rigforge_mocap.py:886`). A not-ready clip is
    left in the file with `status: "success"` and `contract.ready: false`
    (949-964).
  - `headless_mocap.py` covers only the ready path (454-532). `strict` and
    `ready: false` have no test (grep for "strict" in `addon/tests/` finds
    only unrelated hits).
- **Failure scenario.** A caller that checks `status` rather than
  `contract.ready` (the MCP default) ships a clip whose bake was fought by a
  constraint.
- **Fix direction.** Put `transfer_check` in `failed` with a derived
  threshold. Consider `strict: true` as the default for the driver. Add a
  negative test that makes the contract fail (for example a locked control)
  and asserts strict removal.

### 7. Punch's extension cap reads the plan, not the deform chain, and the arms may stretch (MEDIUM)

- **Evidence.**
  - `plant_ik_stretch` keys only `kinds=("leg", "front_leg")` (1258). Punch
    puts both arms on IK (3259-3261) with Rigify's `IK_Stretch = 1.0` left
    live.
  - `extension_m` is `(fist - joint).length` (3434), where `fist` is the
    authored target point, not the deform hand. The cap check at 3499-3510
    uses that number, while the text at 3506 says "Measured on the posed rig".
  - The deform truth, `fist_landed_mm` (3429-3436), is reported but never
    warned on. Only the suite gates it (`headless_punch.py:408-410`).
  - This contradicts convention line 58: extension caps measure the DEF
    chain.
  - Punch also computes m/s with `render.fps` and ignores `fps_base` (3477).
    Jump divides by it (3978-3979).
- **Failure scenario.** On a non-synthetic rig whose arm reach is summed
  short, the fist target sits past real reach. The arm stretches. The command
  reports `extension_within_cap: true`, and there is no warning to show the
  hand was scaled.
- **Fix direction.** Key the arm's `IK_Stretch` to 0 for the clip. Measure
  extension on the DEF upper_arm-to-hand heads. Warn on `fist_landed_mm`
  above a derived tolerance.

### 8. Jump's report writes `landing_strike_deg` twice; the second value hides the floor clamp (MEDIUM, concrete bug)

- **Evidence.** In the return dict at `rigforge_anim.py:5156` the value is
  `round(strike_ratio * roll_deg * ankle["catch"], 3)`, the angle actually
  authored after the floor solve. At 5205 the same key is
  `round(strike_ratio * roll_deg, 3)`, the angle asked for. Python keeps the
  last duplicate key, so the report always shows the unscaled ask.
- **Failure scenario.** The floor clamp scales the heel strike
  (`catch_ankle` in `_build_channels`, 4130-4133; the ankle solve,
  4888-4908) and the report still says the full strike shipped. Any gate or reviewer
  reading this key reads a number that was not keyed. `load_toe_lift_deg`
  (5154) has no such duplicate.
- **Fix direction.** Rename 5205 to `landing_strike_asked_deg`, mirroring
  `load_toe_lift_asked_deg`. Add an assertion in `headless_jump.py` for the
  case where the ankle is scaled.

### 9. `locomotion_frame` falls back to a 1 m leg and a -Y facing without saying so (MEDIUM)

- **Evidence.** At `rigforge_anim.py:1753-1762`, if no toe or tip bone
  resolves, `forward = (0, -1, 0)`. If no `fk_chain` hip resolves,
  `leg_length = 1.0`. Neither sets a flag or adds a warning. Compare
  `rigforge_rig.rig_forward_axis` (2791-2828), which returns
  `how = "convention"` for the same fallback.
- **Failure scenario.** On a small figurine rig whose FK names differ, every
  walk, punch and jump default ("fractions of this rig's leg", 1909-1911)
  becomes a fraction of 1 m: a 400 mm step, 350 mm apex and so on. The output
  still passes the command's own clamps. This is the seed -> 56 default class.
- **Fix direction.** Return `forward_how` and `leg_length_how`, warn when
  either is a fallback, and refuse outright when `leg_length` is the
  fallback.

### 10. Units: the add-on assumes 1 BU = 1 m everywhere; cloth offsets ignore object scale (MEDIUM)

- **Evidence.**
  - `MM_TO_M = 0.001` (`common.py:31`) is applied everywhere.
  - `scene.unit_settings.scale_length` is only read to report it
    (`common.py:464`) and in the export (`rigforge_rig.py:4531-4577`). No
    command refuses or corrects when it is not 1.0.
  - In cloth, `offset_mm` and `thickness_mm` become metres (758-759) but are
    applied in object-local space: `vertex.co + vertex.normal * offset`
    (415), Solidify `thickness` (445-454). Collision distances are derived
    from them (556-558).
- **Failure scenario.** A body object at scale 0.01 (a common FBX import)
  gets a 0.05 mm offset instead of 5 mm, and the sim distance follows it. A
  scene set to millimetre units is off by 1000x on every `_mm` parameter.
- **ASSUMPTION:** whether any live artist scene has `scale_length != 1` or
  scaled bodies. Settle it with `get_scene_info` on the project blends plus a
  scan of `matrix_world.to_scale()` on skinned meshes.
- **Fix direction.** Guard with a refusal when `scale_length != 1`, and
  divide local-space offsets by the object's scale along the normal.

### 11. Sole vertices are selected on the base mesh and read on the evaluated mesh; the selector is duplicated (MEDIUM)

- **Evidence.**
  - Jump's `_sole_indices` (`rigforge_anim.py:4557-4588`) and
    `rigcheck.sole_vertex_indices` (`rigcheck.py:3595-3640`) are the same
    algorithm written twice. The docstring at 3598-3600 promises "one set
    rather than two".
  - Both index the evaluated mesh with base-mesh indices
    (`rigforge_anim.py:4608-4613`, `rigcheck.py:3663-3667`). They only skip
    indices that are out of range (the rigcheck one is marked
    `# pragma: no cover`).
- **Failure scenario.** A topology-changing modifier after the Armature
  modifier (Subdivision, Mirror without apply) keeps indices in range but
  points them at different vertices. The floor clamp and the anticipation
  gate then measure arbitrary vertices, silently.
- **ASSUMPTION:** that such a stack exists on a production mesh. Settle it by
  listing modifier stacks on the skinned meshes.
- **Fix direction.** One shared selector. Assert that evaluated and base
  vertex counts match, or report the selector as unmeasured with a reason.

### 12. Socket responses: non-JSON values become strings and NaN goes out as bare `NaN` (LOW-MEDIUM)

- **Evidence.** `server.py:270` calls
  `json.dumps(response, default=_json_fallback)`, and `_json_fallback` is
  `repr` (327-328). A handler that leaks a `Vector` or `Matrix` succeeds with
  a string like `"Vector((0.0, ...))"` in place of a list. `allow_nan`
  defaults to true, so a NaN produced upstream is emitted as a `NaN` token,
  which is not valid JSON.
- **Failure scenario.** The Python MCP parser accepts NaN. A browser
  `JSON.parse` in `assistant/webui` would not (**ASSUMPTION** that bridge
  results reach it verbatim). A caller doing arithmetic on a repr string
  fails far from the cause.
- **Fix direction.** Use `allow_nan=False` and turn a failure into an error
  response naming the key. Convert mathutils types explicitly and make the
  fallback an error, not `repr`.

### 13. Registry undo: a global kill switch, a push before validation, and measurement commands missing from `READ_ONLY` (LOW-MEDIUM)

- **Evidence.**
  - One refused `undo_push` flips `_UNDO_AVAILABLE = False` for the rest of
    the session, and the only trace is a print (`registry.py:139-150`).
  - `push_undo(name)` runs before the handler validates its params (227), so
    a rejected call still leaves an undo step.
  - `animation_check`, `motion_stats` and `get_activity` are measurements
    that restore state, like `rig_check`, which is listed (79-81). They are
    not in `READ_ONLY_COMMANDS` (40-118), so each call buries the artist's
    last undo step.
- **Failure scenario.** After a single transient refusal, the Assistant's
  "Revert last AI action" reverts something older, silently.
- **Fix direction.** Retry per call instead of latching off, and surface the
  state in `ping` and `status`. Add the three commands to the list.

### 14. `execute_python`: a namespace that persists across calls, and an empty result that looks like success (LOW-MEDIUM)

- **Evidence.** At `common.py:472, 495-498`, `_EXEC_NAMESPACE` persists
  between calls unless `reset: true` is passed. Names from an earlier call
  shadow later code. At 505-532 a script whose last statement is not an
  expression returns `{"output": "", "result": None}`. That is identical to a
  script that did nothing, and it is the empty-result shape that cost
  debugging time this week.
- **Fix direction.** Return `tail_evaluated: bool`, `statements: n` and
  `namespace_reused: bool`, and default `reset` to true for MCP calls.

### 15. Three rig resolvers with different precedence and an active-object fallback (MEDIUM-LOW)

- **Evidence.**
  - `rigforge_anim._rig_for` (215-244): explicit, then mesh modifier, then
    `forge_rig` prop, then the **active armature**, then the only armature.
  - `rigcheck._resolve_rig` (280-305): the active armature only if it has a
    rigged mesh.
  - `rigcheck._resolve_rig_loose` (4595-4614): the only armature before the
    active one.
- **Failure scenario.** With two armatures and a mocap import selected,
  authoring picks the active one while the gate picks by a different rule.
  Results echo `rig`, but nothing asserts that author and gate agreed.
- **Fix direction.** One resolver with one precedence order, and
  `rig_resolved_by` in every result.

### 16. The panel's Retarget button runs the pre-mocap defaults: FK legs and auto mapping (MEDIUM-LOW)

- **Evidence.** `FORGE_OT_rf_retarget` (`rigforge_anim.py:7453-7458`) passes
  only path, name and loop. The command defaults are `legs: "fk"` (6041),
  `mapping: "auto"` (6023) and `root_motion: "keep"` (6038). The production
  driver defaults to IK legs and in-place (`rigforge_mocap.py:882-885`).
  Keying the legs in FK is the foot-slide anti-pattern the module's own
  errors describe (1960-1966).
- **Fix direction.** Point the button at `rigforge_mocap_clip`, or give the
  command production defaults.

### 17. Duplication in rigforge_anim.py (cost; no single bug)

Measured repeats:

- **Clip epilogue (×3):** the interpolation loop (2528-2536 / 3438-3446 /
  4974-4982), the frame restore plus `restore_ik_stretch` (2538-2544 /
  3448-3454 / 4984-4990), and the missing-stretch warning (2550-2555 /
  3460-3464 / 4996-5000).
- **Action get-or-create plus fake user (×4):** 1386-1391, 2008-2013,
  3226-3235, 4186-4194. Keyframe and walk do not set the fake user on an
  existing action. Punch and jump do.
- **Heel/toe quaternion to XYZ switch (×3):** 2107-2115, 3309-3317,
  4298-4313.
- **FK arm discovery (×2):** 2086-2100, 4278-4289. The `_control` closure
  (×2): 3274-3278, 4239-4243.
- **Hip-over-planted-foot triangle solve (×5):** `_reach_limit` and
  `_crouch_for` (1784-1853), `_punch_stance` (2886-2940),
  `_extension_headroom` (3846-3863), and retarget inline (6555-6557,
  6804-6806). See #5.
- **Forward axis (×4):** `locomotion_frame` (1700-1761),
  `rigforge_rig.rig_forward_axis` (2791-2828), the
  `rigforge_mocap.rigforge_rig_forward` wrapper (807-813), and retarget
  `_basis` (5685-5700). `rig_forward_axis`'s docstring claims "the same rule"
  as `locomotion_frame`, but the two read different bones: control toe or tip
  bone versus the DEF foot and toe.
- **Same name, different meaning:** `BODY_CONTROLS` is
  `("torso","hips","spine_fk")` in anim (2790) and
  `("root","torso","hips","DEF-spine","spine_fk")` in rigcheck (2057).
- **Drift the jump already fixed:** the walk's reach loop still runs a fixed
  `range(4)` pass budget (2455). The jump's rationale for replacing exactly
  that pattern is at 3742-3749.

### 18. Dead and stale code after the mocap pivot, and what is still load-bearing (LOW)

- **Dead.** `rigforge_mocap.PRESET_STATUS` (329), `LIMB_SLOTS` (337) and
  `SEGMENT_END` (340) have zero references in addon, mcp, projects or
  assistant. The module docstring (14-17) and the inline comments at 252 and
  304 still say "ASSUMPTION ... not verified". `PRESET_STATUS_BY_NAME`
  (321-325) records cmu as verified (8 CMU takes) and 100style as read off
  the files. `_import_clip`'s `warnings` parameter is unused (5436).
- **The procedural gait path.** The production build no longer calls
  `rigforge_walk`. Locomotion is `rigforge_mocap_clip`
  (`build_protagonist_human.py:15-22, 277-281`). The only project caller,
  `clips_protagonist_human.author_gait` (172-179), is not invoked by the
  build, which calls `author_fall`, `author_land` and `knee_flex_max`
  (308-314). `rigforge_walk` survives as:
  - the red-proof generator for `gait_opposition` and `strike_lead`
    (`headless_animgates.py`, 33 fail-path references);
  - the subject of `headless_rigik`;
  - an MCP tool (21 references in `mcp/forge_mcp/server.py`).
- **Still load-bearing.** The shipped `jump` and `punch.L`/`punch.R` are
  earlier `rigforge_jump`/`rigforge_punch` outputs exported unmodified
  (`build_protagonist_human.py:386-388, 473`). The walk-section helpers are
  imported **by private name** from project scripts: `_set_world`,
  `_key_transform`, `_rest_world`, `_world_matrix`, `_rotate_about`,
  `locomotion_frame` (`build_protagonist_human.py:195-265`,
  `clips_protagonist_human.py:120-524`). The build also monkeypatches
  `rigforge_rig.op_kwargs` (460-478).
- **Replaceable.** The walk's stride/crouch/foot-roll authoring
  (1566-1897 constants and helpers, command 1900-2663; about 1,100 lines).
  Freeze it as a gate fixture rather than deleting it.

### 19. motion_quality passes with a floor unmeasured; the neck bone name is hard-coded (LOW-MEDIUM)

- **Evidence.** `rigforge_mocap.py:579-580` returns
  `verdict = "ok"` when any metric was measured and none failed. With one or
  two floors `unmeasured`, the gait passes on the rest. `trunk_pitch` needs
  `DEF-spine.003` by literal name (666). `head_pitch` takes the last
  `DEF-spine.N` (667-670).
- **Failure scenario.** On a rig whose spine has a different segment count,
  `trunk_pitch_std_deg` is None and the "rigid torso" floor silently drops
  out.
- **Fix direction.** Require all pinned floors (unmeasured means not ok), and
  resolve the neck through `def_bones_for`.

### 20. Size and structure (cost)

- `rigforge_anim.py` is 7,499 lines. By section:
  - cloth: 197-830 (634)
  - action library: 832-1107 (276)
  - keyframe and IK/FK helpers: 1110-1525 (416)
  - walk: 1528-2663 (1,136)
  - punch: 2666-3590 (925)
  - jump: 3593-5245 (1,653)
  - retarget: 5248-7128 (1,881)
  - panel: 7131-7499 (369)
- Single functions: `cmd_rigforge_jump` is 1,379 lines (3867-5245),
  `cmd_rigforge_retarget` 1,192 (5937-7128), `cmd_rigforge_walk` 764,
  `cmd_rigforge_punch` 647. Elsewhere, `rigcheck.cmd_animation_check` is 877
  (3718-4594).
- **The split pays**, for the lane rules rather than for taste. "Never two
  lanes in one file area" makes one 7.5k file a serialisation point for every
  animation lane. The local-worker integrity gate (convention lines 94-97)
  also works better on smaller modules.
- **The constraint** is #18's private-name imports: the split must leave
  `rigforge_anim.py` as a facade that re-exports them, or the production
  build breaks.

---

## Test blind spots (suite map vs tools)

- **No suite at all.**
  - `pov.py` (194 lines, `playtest_pov`): zero references in `addon/tests/`.
  - Commands in `common.py` with zero test references: `symmetrize`,
    `shade`, `apply_transforms`, `set_origin`, `merge_by_distance`,
    `separate_loose`, `delete_object`.
- **One suite only.**
  - Commands covered by a single suite: `rigforge_cloth`, `rigforge_action`,
    `rigforge_correctives`, `sprite_cutout`, `fit_to_silhouette`,
    `track_points`/`grab_to`/`probe`, `measure_peg`, `motion_stats`,
    `rigforge_mocap_clip`, `save_project_blend`/`open_project_blend`.
  - Modules imported by a single suite: bosses, correctives, floorplan,
    mechanism, projects, rigforge_joints, rigforge_mocap, seating,
    spriteforge, trackforge.
- **Gates and paths with no negative test.**
  - Export independent of action order and live pose (#1). Only
    `headless_mocap.py:615-635` proves this, and only for `rigforge_retarget`.
  - A rig_check placement gate that raises (#4).
  - `rigforge_mocap_clip` with `ready: false` or `strict: true` (#6).
  - `transfer_check` failing (#6).
  - Server job timeout (#3).
  - `repo_root` outside a checkout (#2).
  - Cloth on a scaled body (#10).
  - Floor clamp with a topology-changing modifier (#11).
  - The mixamo preset. `PRESET_STATUS_BY_NAME["mixamo"]` is still an
    assumption; `headless_mocap.py:360` exercises a synthesized Mixamo-style
    take.
- **Negative tests that exist.**
  - `gait_opposition` and `strike_lead` red proofs (`headless_animgates`).
  - The motion-quality tier failing the procedural walk
    (`headless_mocap.py:578-586`).
  - BVH refusals (241-303).
  - Retarget blindness to the live pose (615-635).
  - Punch deform landing (`headless_punch.py:408-410`).

---

## Prioritized refactor plan

**Lanes** (one deliverable each, per the lane contract):

1. **Author and export from rest.**
   - Deliverable: the rule from #1 and its gate.
   - Files: `rigforge_anim.py`, `rigforge_rig.py` (export loop), one new
     `headless_*` suite on the next free port after 9914.
   - Evidence: order-swapped export digests equal; walk authored after a
     displaced live pose equals walk authored from rest.
   - Must keep the private helper names intact.
2. **rig_check error ≠ unmeasured.**
   - Deliverable: #4 plus a negative test.
   - Files: `rigcheck.py`, `headless_landmarks.py` or a new suite.
3. **Server job cancellation.**
   - Deliverable: #3 plus a test.
   - Files: `server.py`, a test.
   - Small, but it touches the live-socket contract, so it gets a lane
     rather than a worker pass.
4. **One leg-reach model.**
   - Deliverable: #5, research first: measure `max_span` against the rest
     sum on the werewolf and re-pin `REACH_CAP` and the `ik_reach_headroom`
     bands with the measured derivation.
   - Files: `rigforge_anim.py` (jump and retarget caps), `rigcheck.py`.
   - Evidence: `headless_jump`, `headless_mocap`, `headless_animgates`
     green, with the new numbers quoted.
   - Blocked on lane 1 (same file area).
5. **Split `rigforge_anim.py`.**
   - Deliverable: a pure move into cloth / anim_core (action library,
     keyframe, pose helpers, the shared epilogue from #17) / walk / punch /
     jump / retarget, with `rigforge_anim.py` kept as a re-export facade.
   - Evidence: byte-identical action digests from `headless_rigik`,
     `headless_punch`, `headless_jump`, `headless_mocap`, `headless_phase5`,
     and `build_protagonist_human.py` importing unchanged.
   - Only after lanes 1 and 4 land.
6. **Mocap contract hardening.**
   - Deliverable: #6 (transfer_check in `failed`, derived threshold, strict
     tests) and #19 (all floors required).
   - Files: `rigforge_mocap.py`, `headless_mocap.py`.

**Chores for the local worker.** Each one is under 20 lines of diff. The gate
is the owning module's real suite plus a line-count check, per convention
lines 94-101.

- #8: rename the jump's duplicate `landing_strike_deg` key to
  `landing_strike_asked_deg` (`headless_jump`).
- #7 (part): punch fps divides by `fps_base` (`headless_punch`).
- #2: `repo_root` marker check (`headless_projects`).
- #18: delete `PRESET_STATUS`, `LIMB_SLOTS` and `SEGMENT_END`, and correct
  the stale ASSUMPTION docstrings (`headless_mocap`).
- #13: add `animation_check`, `motion_stats` and `get_activity` to
  `READ_ONLY_COMMANDS` (`headless_activity`).
- #12: `allow_nan=False` plus an explicit error (a suite that already
  round-trips the socket).
- #9: warn and flag on `locomotion_frame` fallbacks (`headless_rigik`).
- #16: point the panel Retarget at the production defaults
  (`headless_ui_batch`).

**Not worth a lane now.** #10 unit guard (settle the ASSUMPTION first), #11
evaluated-mesh assertion (settle the ASSUMPTION first), #14 execute_python
result shape (coordinate with the MCP side), #15 resolver unification (fold
it into lane 5's anim_core).
