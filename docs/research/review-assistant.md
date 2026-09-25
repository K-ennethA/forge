# Review: forge assistant layer (bridge.py, webui, system prompt)

Read-only review, 2026-09-25. Scope: `assistant/bridge.py`, `assistant/webui/{app.js,glbview.js,format.js}`,
`assistant/system_prompt.md`, with `benchmark/runner.py` and `mcp/forge_mcp/` read where the bridge's
behaviour depends on them. Nothing was changed. Every claim cites file:line; anything not proven from
code or a measurement is labelled ASSUMPTION with the experiment that would settle it.

Measured size: `bridge.py` is **11,299 lines** (not ~8k): 6,697 code, 2,003 docstring, 1,332 comment,
1,267 blank. It includes 460 lines of Blender Python held as `%`-format strings
(`SNAPSHOT_SCRIPT` 6163-6260, `JOINT_MOVE_SCRIPT` 6538-6689, `WEIGHTS_LOCAL_SCRIPT` 7219-7424).
`app.js` 5,316 lines, `glbview.js` 1,745, `system_prompt.md` 116,890 bytes.

---

## Ranked findings

### 1. The unauthenticated localhost surface can be driven by any web page, and it reaches code execution
- **Where:** `_read_json` ignores Content-Type (bridge.py:8928-8949). No handler checks Host, Origin or a
  token (grep for `Origin`/`Host` finds nothing). `/ask` routes at 9113. Allowed tools include
  `mcp__forge__*` (533), which includes `execute_blender_python` (mcp/forge_mcp/server.py:427, "Run
  arbitrary Python inside Blender"). Turns run with `--permission-mode auto` (537, 2311).
- **Failure:** a page the artist has open POSTs `text/plain` to `http://127.0.0.1:8901/ask`. That is a
  CORS "simple request", so there is no preflight. The bridge parses the body as JSON anyway and starts
  a turn that can call `execute_blender_python`, which is arbitrary code running as the user. With DNS
  rebinding the page can also read responses: `GET /jobs` returns every reply and path, and `/file/<token>`
  returns the files. `/ask`'s `context.image_path` mints a token for any image anywhere on disk
  (9326-9327; `image_error` 2161-2183 checks only the extension and that the file exists). That is the
  one place a client-chosen path becomes servable.
- **ASSUMPTION:** whether a given browser's local-network-access protection blocks the cross-origin
  POST depends on the browser and version. To settle it, open a page on another origin that runs
  `fetch("http://127.0.0.1:8901/ask",{method:"POST",body:'{"message":"status?"}'})` and check
  whether a job appears in `/jobs`.
- **Fix:** refuse any request whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` (this
  defeats rebinding). Refuse any request with an `Origin` other than the bridge's own. Require
  `Content-Type: application/json` on POST, which forces a preflight that the bridge never answers.
  Optionally add a per-process token that `index.html` and the add-on read.

### 2. `/ask` defaults to `continue` on one global session, and errors keep that session
- **Where:** there is one `session_id` for the whole process (3205). The default is
  `conversation: "continue"` (9287-9288). The error path runs `remember_session(payload.session_id)`
  (3883-3884). Server-composed turns (`/projects/create`, `/projects/attach`, `/build`,
  `/task_config`) are hard-wired to `new_conversation=False` (10416). The benchmark sends `"new"`,
  which zeroes the session and its cost (3352-3355; runner.py:97).
- **What else depends on the default:**
  - The web UI always sends `continue` (app.js:420).
  - The add-on and buddy check-ins default to `continue` (addon/forge/tools/assistant.py:530, 570).
  - The live-context `blender_seen_at` is per global session (3223).
  - The session cost shown in the UI is per global session.
- **Failures:**
  1. **Poison loop.** A turn fails deterministically (context too large, a broken attachment in the
     history). The session is still remembered, so every later `continue` from any client resumes
     it and fails the same way until someone presses New. There is no consecutive-error counter.
  2. **Cross-client bleed.** A benchmark run resets the artist's conversation and zeroes their cost
     display. The artist's next message then resumes the benchmark task's transcript.
  3. **Wrong-project context.** A new project's design turn (`/projects/create`) resumes whatever
     conversation was last active, which may be another project's or a benchmark's.
- **Verdict:** `continue` is right for the one interactive thread. It is wrong as the default for API
  callers and composed turns, and it is wrong to share one slot among all clients.
- **Fix:** keep conversations keyed by id (`conversation_id` maps to `session_id`). Each UI surface
  holds its own id, the benchmark always gets a fresh one, and each project gets one for composed
  turns. Make `conversation` required for non-UI callers. When an error is non-transient, or after
  N consecutive errors, stop resuming and say so.

### 3. Pressing New during a running turn brings the old session back
- **Where:** `reset_session` (3226-3236) versus the worker's `remember_session(new_session)`
  (3898, and 3884 on error). `_record_outcome` then adds that turn's cost to the fresh total (3543).
- **Failure:** the artist presses New while a turn is running. When the turn finishes it re-installs
  the session the artist just discarded, and its cost is added to the new conversation's total. A
  queued `continue` message then resumes the discarded conversation. `/new` does not cancel the
  running or queued job either (9124-9128).
- **Fix:** add a session epoch that increments on every reset and is stamped on the job at start.
  `remember_session` and the cost fold apply only when the epoch still matches.

### 4. Cancel, timeout and bridge death kill only the direct child, so CLI processes are orphaned
- **Where:** `_terminate` calls `proc.terminate()` and then `kill()` on the one PID (3577-3589).
  `Popen` uses no job object and no process group (3811-3818). Turn threads are daemons (3955-3960).
  `serve()` has no cleanup: no atexit, no cancel of the active job (11258-11275). Jobs are held only
  in memory (3592).
- **Failures:**
  - When the bridge dies or is paused, the running `claude.exe` keeps running. Its MCP server and
    tool children keep driving Blender with no reader and no watchdog. This matches this week's
    hand-kill.
  - On cancel or timeout on Windows, TerminateProcess does not reach grandchildren (the MCP server,
    Bash-tool shells). If `claude.cmd` is what resolves (candidate list 1861), only `cmd.exe` is
    killed and `node` survives. The launcher's own docstring (2059-2067) says `cmd.exe` cannot carry
    a newline in an argument, so an npm-shim install would also truncate every multi-line prompt.
    On this machine `~/.local/bin/claude.exe` resolves, so the shim path is latent.
  - **ASSUMPTION:** the stdio MCP server exits on stdin EOF once its parent dies. To settle it,
    cancel a turn mid-tool-call, then list `python.exe` processes whose command line has `forge_mcp`.
- **Fix:** assign every CLI child to a Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
  (ctypes is stdlib). Children then die with the job handle, and so with the bridge. Use
  `taskkill /T /F /PID` as the fallback for cancel. Write a pidfile and reap stale children at
  startup. On shutdown, cancel the active job.

### 5. Cost, turn count and model are missing on every terminal state except `done`
- **Where:**
  - **timeout** (3837-3846): cost comes from `ActivityRecorder._money` (2743-2762), which looks for
    `total_cost_usd`/`cost_usd` on any event. In practice only the final result event carries it, so
    cost is `None` (observed this week). `usage` is overwritten per message (2757, 2762), so it holds
    the last message's usage, not the turn's. No `num_turns`, no `model`.
  - **error** (3882-3883): `total_cost_usd` and `num_turns` from the payload are dropped, and
    `_record_outcome` bills errors at nothing (3549). `error_max_turns` is the most expensive error
    class and is billed at $0.
  - **cancelled** (3851): nothing is recorded, and the streamed reply is thrown away. The only
    cancel-mid-stream test asserts on activity alone (tests/test_bridge.py:1605-1624).
- **Consequence:** the session and per-model totals undercount. The benchmark report's
  `bridge.cost_usd` and `num_turns` (runner.py:231-235) are null for every timeout, error and cancel
  run. The fix for the dogfood report's $0 billing (docstring 3531-3534) does not work in practice.
- **Fix:**
  - Capture the `system/init` event (session_id, model).
  - Accumulate usage per message id from message_start and message_delta.
  - Count assistant messages as `num_turns`.
  - On error, keep the payload's cost and turn count.
  - On cancel, land the checkpoint the way timeout does.
  - Return `cost_estimated: true` whenever cost comes from summed usage and a price table rather
    than the CLI.

### 6. A turn that has already produced its result can still land as `timeout`
- **Where:** `run_turn` checks `outcome["timed_out"]` (3832) before it looks at `outcome["result"]`.
- **Failure:** the result event is read, but the process lingers past the budget or stall clock while
  shutting down children. The watchdog kills it, and the turn lands as `timeout` with checkpoint text.
  The real reply, cost, `num_turns` and model were already parsed and are discarded. A cancel in the
  same window turns a completed turn into `cancelled` (3509-3512) and drops its cost.
- **Fix:** if `outcome["result"]` is present, finish as `done` whatever the clocks say.

### 7. The 300 s stall clock is shorter than tools the assistant legitimately runs
- **Where:** `DEFAULT_STALL_TIMEOUT = 300` (528) and turn budget 600 (522). The MCP side waits up to
  900 s for a meshgen job (mcp/forge_mcp/config.py:82), 900 s for `flow_run` (:130), and 330 s for
  segment and mold (:58, :63). server.py sends no MCP progress notifications (no `report_progress`).
- **Failure:** a single silent tool call longer than 300 s is killed. The checkpoint then tells the
  artist the CLI "went silent … would have stalled at any size" (2486-2493), which is a misdiagnosis.
  A 900 s tool call cannot finish inside a 600 s turn at all. The derivation comment (521-527)
  describes 595 s of silence that was followed by a successful turn, which argues against the number.
- **Fix:** pause the stall clock while a `tool_use` is open. The recorder already sees each tool's
  start and its result. Accept `timeout_s` on `/ask` so callers such as the benchmark can size the
  budget per task.

### 8. The benchmark runner and the bridge run on different clocks
- **Where:** the runner's deadline is `task.timeout_s + 60` (runner.py:172). `/ask` accepts no
  timeout, and the bridge uses `FORGE_ASSISTANT_TIMEOUT` (1690-1695). After cancelling, the runner
  reports from its last pre-cancel poll (runner.py:190-199, cancel at 194) and never re-polls.
- **Failure:** when a task's timeout is shorter than the bridge budget, the runner cancels. The job
  lands `cancelled` with no cost (finding 5). The report records `timeout`, taken from a snapshot
  still showing `running`, with null cost and turn count.
- **Fix:** pass `timeout_s` through `/ask`. After a cancel, the runner should poll until the job
  reaches a terminal state.

### 9. The cancel route exists; the 404 comes from the in-memory store or a wrong path, and runaways have no cap
- **Where:** `POST /cancel/<id>` is at 9116-9123. A 404 comes from `JobStore.cancel` returning None,
  body `"No such job."`. That happens when the bridge restarted (no persistence, 3592) or the job was
  evicted past `MAX_JOBS = 20` (722, 3286-3292). Any other path gets the catch-all 404, body
  `"Unknown path /..."` (9247).
- **Root cause of this week's 404: unverified.** The 404 body settles it: "No such job." means the id
  was unknown; "Unknown path" means a wrong route or verb.
- **What stops a runaway today:** the 600 s wall clock, the 300 s stall clock, and a cancel that needs
  the job id. There is no `--max-turns`, no spend cap per session or per day, and no "cancel whatever
  is running" route.
- **ASSUMPTION:** the installed CLI supports `--max-turns` (and possibly a budget flag). To settle it,
  run `claude --help`.
- **Fix:** add `POST /cancel` with no id to stop the active and queued jobs. Add a session spend cap
  that refuses `/ask` with 402/409 once it is reached. Pass `--max-turns`.

### 10. The timeout checkpoint over-promises
- **Where:** `checkpoint_reply` (2486-2500) tells the artist "the conversation survived … ask 'what
  got built?'". The timeout path finishes with `session_id=JOBS.session_id` (3844) and never records
  the stream's own session id.
- **Failure:** when the timed-out turn was the first of a new conversation, `JOBS.session_id` is
  still None. The artist's follow-up starts a blank conversation.
- **Other integrity gaps:**
  - Steps are capped at 40 with no marker (1598, 2857). The steps dropped are the latest ones, which
    are the most relevant.
  - The tool that was in flight when the turn was killed is listed under "Steps it completed"
    (2484-2485), because it has no result.
- **ASSUMPTION:** resuming a transcript that was killed mid-`tool_use` makes the CLI error, and so
  poisons the session (finding 2). To settle it, kill a turn during a long tool call, then send
  `continue`.
- **Fix:** record the init event's session_id, mark steps with no result as `(interrupted)`, and add
  an "…N more" marker when steps are capped.

### 11. Three POST paths leave the body in the keep-alive socket
- **Where:**
  - The catch-all 404 in `do_POST` (9247).
  - `_set_params` sends its 404 (9511) before reading the body (9514).
  - `_add_ref` sends its 404 (10201) before `_read_body` (10205).
- **Failure:** this is the bug `_read_json`'s own docstring describes (8932-8936). The next request on
  that connection is parsed from the leftover JSON and gets a 501 on an unrelated route. A client
  that hits a mistyped route poisons its connection.
- **Also:** `_read_json` trusts `Content-Length` with no cap (8944).
- **Fix:** drain the body once in `do_POST`, before dispatch, with a size cap. Handlers then receive
  the parsed body.

### 12. The artist-edit journal drops placements and has unlocked read-modify-write
- **Where:** `append_artist_edit` keeps the last 2000 records (1266, 6485-6486). That contradicts its
  own contract (1259-1260: it "never alters or drops one it did not write") and lane-conventions
  ("Artist placements … are inputs"). Neither the journal nor the refs manifest (7954-7975) has a
  lock; the file has only four locks, and none covers these files. The server is a
  ThreadingHTTPServer (11261).
- **Failure:** past 2000 edits the oldest artist placements, including hand-written ones, vanish from
  the regeneration input. Two direct-edit requests racing each other can lose one record.
- **Fix:** never truncate; rotate old records to an archive file if size matters. Add a per-file
  lock around read-modify-write.

### 13. Joint nudge applies a world-space delta in armature-local space
- **Where:** the viewer builds the delta from world positions (glbview.js:723, 1058-1077, mapping at
  733-739). The bridge adds it to edit-bone head and tail with only the unit-scale conversion
  (bridge.py:6553-6556, 6632-6645). There is no `arm.matrix_world` inverse.
- **Failure:** on any rig whose object carries rotation or scale, the bone moves the wrong way or the
  wrong distance. The drop-in build yaws the rig object 180° (`rig.rotation_euler = (0,0,180°)`,
  build_werewolf_escaped.py:219 at commit 7756cd2). On such a rig a "forward" nudge moves backward and
  "side" moves the mirror way. `mirrored_delta` flips X in whatever space it is given.
- **ASSUMPTION:** that the artist's live scene has a rotated or scaled rig object. To settle it, run
  `print(rig.matrix_world)` in the scene the workspace snapshots.
- **Fix:** in the script, convert with `arm.matrix_world.inverted().to_3x3() @ Vector(step)` before
  adding. The mirror delta should be computed in local space.

### 14. The weight brush misses when a clip is posed
- **Where:** `surfacePoint` skins the mesh with the current clip pose (glbview.js:979-986). The Blender
  side tests the radius against rest positions, `matrix_world @ vertex.co` (inside
  `WEIGHTS_LOCAL_SCRIPT`, 7219-7424, the `(matrix @ vertex.co - point).length` test).
- **Failure:** after scrubbing a clip, the brush repairs the wrong vertices or answers "Nothing is
  within that radius".
- **Fix:** pick against rest positions. `restPose` before `applySkin` for picking is one way.

### 15. Direct edits act on the live scene but are journaled to whichever project the URL names
- **Where:** the snapshot exports every visible object in the live scene regardless of project
  (6175-6182). `joint_move` picks the armature from the live scene (6558-6584). The journal write
  goes to the URL's project (10755; also 10907, 10981, 11049). `wsSnapshot` has no staleness guard
  (app.js:2964-2985), unlike the Studio's (app.js:959-961).
- **Failure:** with project B selected and project A's rig open in Blender, A's placements land in
  B's `artist-edits.json`, which is a binding regeneration input.
- **Also:** direct edits never check `JOBS.is_busy()`, so they can interleave with a running turn's
  multi-command operation.
- **Fix:** have the script report `bpy.data.filepath` and the rig name. Refuse, or ask, when the live
  file is not under `projects/<name>/`. Store the rig and file in each record.

### 16. glbview correctness against the exporter
- **Tangents:** they have no effect. The viewer reads no `TANGENT` and draws no normal maps, and it
  only ever loads the bridge's own `SNAPSHOT_SCRIPT` export (6232-6235: `export_apply=True`,
  `export_yup=True`), not the game drop-in export whose `export_tangents=True` changed this week
  (addon/forge/tools/rigforge_rig.py:4542-4551).
- **Facing flips:** a flip done by rotation, like the 180° yaw above, draws correctly: skinned meshes
  get an identity model matrix and joint worlds times IBM, per spec (1216-1221, 414-432). A flip done
  by negative scale does not. Back-face culling is on (1184-1185) and the node determinant is never
  checked, so a mirrored node renders inside-out.
- **Other gaps:**
  - `CUBICSPLINE` samplers are read as linear (390, 495-501). **ASSUMPTION:** the snapshot's default
    sampling keeps them from appearing.
  - Morph targets are ignored (380), so correctives do not show during playback.
  - `restPose` does not restore `node.matrix` after `poseAt` nulls it (522, 527-534).
  - Handles and pins sit at rest positions while a paused clip shows the posed mesh (1461, 1506-1531).
- **Fix:** flip `gl.frontFace` per primitive when `det(world) < 0`. Implement CUBICSPLINE or refuse
  it with a message. Keep the original matrix for `restPose`.

### 17. app.js state handling
- **Where:** `poll()` never gives up on a job whose `GET /job` fails (app.js:378-382). After a bridge
  restart the spinner stays up and polls every 800 ms indefinitely. `setInterval` polls can overlap
  with no ordering guard (387), so an older `running` snapshot can overwrite `done`. Jobs started by
  other clients never appear until reload: `loadJobs` is called only at 5289, 4188, 4380 and 4773,
  despite the header claiming a shared thread (app.js:10-11). Nine module-level mutable state objects
  live in one 5,316-line IIFE (52, 66, 79, 86, 2151, 3209, 4005, 4213, 4403).
- **Fix:** mark a job `lost` after N non-200 polls. Chain `setTimeout` instead of `setInterval`, and
  drop out-of-order snapshots by `finished_at` or state rank. Refresh `/jobs` whenever `/services/health`
  reports `busy` on a job the page does not know.

### 18. Per-turn fixed cost
- **Where:** the 116,890-byte system prompt is appended to every turn (2306-2307), including
  auto-routed cheap status turns. **ESTIMATE:** about 29k tokens at 4 characters per token.
  Auto-routed haiku turns still `--resume` the full conversation (2318).
- **ASSUMPTION:** a conversation longer than the cheap model's context window makes a routed status
  question fail, and under finding 2 that error is kept. To settle it, route a status question on a
  long session.
- **Fix:** trim or split the system prompt by workflow. Skip auto-routing when the session is large.

### 19. Live glance consumed by rejected asks
- **Where:** `_ask` advances `blender_seen_at` (9300-9301) before `submit` (9309). A 409-rejected
  message still consumes the Blender activity delta, so the next accepted turn is told nothing
  happened in that window.
- **Fix:** note the glance only after `submit` returns running or queued.

### 20. Dead and duplicate code
- `scan_library` is defined twice. 7534-7535 is a docstring-only stub that returns None; 7538
  replaces it.
- `authoring_kind` (7108) and `forget_schema` (4800) are referenced only by tests.
- The 443-line module docstring duplicates `assistant/README.md` (1,253 lines) and has drifted.
  Neither mentions the conversation-sharing hazard.

---

## Path-traversal audit (priority 4): the gates are consistent

Every file-serving or file-writing entry point applies an alphabet gate and then checks that the
resolved path's parent equals the expected directory:
- `webui_asset` (3986-4009)
- `project_dir` (4631-4654), including the Windows trailing-dot rule
- `thumbnail_path` (4854-4870). It lacks the trailing-dot rule, but `.png` is always appended, so
  this is safe.
- `resolve_version_file` (5860-5879)
- `ref_path` (7872-7891)
- `resolve_indexed_model` (5607-5629), matched by directory equality against the index
- `resolve_dropped_upload` (8272-8295)
- `project_models_dir` (5585-5604) and `create_project_folder` (8439-8449)
- `new_snapshot_path` (6273-6290)

Writes from request bodies all go through these gates:
- `/upload` and `/refs`: sanitised names plus magic-byte checks (4035-4071).
- `/projects/create`: writes `prompt.md` verbatim to a fixed path.
- `/models/file` and `/versions/restore`: copy only from indexed or validated sources.
- `/projects/attach`: reads any `.blend` on disk by design and writes only inside the new project.

Bone and object names reach Blender scripts only through `json.dumps`, and most pass the
`_BONE_NAME_RE` gate first.

Exceptions:
- **The real gap is the token mint from `/ask`'s `image_path`** (finding 1).
- Containment uses `abspath`, not `realpath`, so a junction inside `projects/` is followed.
  **ASSUMPTION:** this is low risk, because the user owns those directories.

---

## Dead or unused routes

Clients checked: `app.js`, the add-on (`addon/forge/tools/assistant.py`), `benchmark/runner.py`.

| Route | Status |
|---|---|
| `POST /scene` | Unused: only tests call it. The web UI uses `GET /scene` (app.js:1212). |
| `/project/<name>/...` singular alias (1155, 9079-9097, 9203-9246) | Unused: only tests call it (test_workspace.py:520, 2031). All clients use `/projects/`. |
| `GET /health` | Not called by the web UI, but used by the add-on (assistant.py:937) and the startup guard (11237). Keep. |
| `POST /flows` | A read served as POST. Used by the web UI (app.js:639). Keep, but it could be GET. |

Every other route in the table has a web UI caller: `/ask`, `/job`, `/jobs`, `/cancel`, `/new`,
`/upload`, `/services/*`, `/flows/run`, `/workflows[/suggest]`, `/projects[/create|/attach]`, the
`/projects/<n>/*` routes (schema, set_params, thumbnail GET/POST, open, save, versions[/restore],
pipeline, deliverables, snapshot, joint_move, inspect, author, weights_local, mesh_fix,
refs[/tag|/note], task_config GET/POST, build), `/preview`, `/scene/delete`,
`/models/{import,file,open}`, `/library`, `/library/cards` and `/authoring`.

---

## Split plan for bridge.py

It pays, and the evidence is in the findings above:
- Finding 11 exists because each of about 35 handlers re-implements drain, 404 and error mapping.
  `except BlenderDown` and `except BlenderRefused` each appear 20 times.
- Findings 2-10 all live in the job, CLI and session core, which is tangled with 8k lines of
  file-browser code.
- 460 lines of Blender Python cannot be linted or tested as strings.

Proposed package `assistant/forge_bridge/`, with `assistant/bridge.py` kept as a re-exporting shim.
The five test files do `import bridge` (tests/test_bridge.py:37 and others) and `start_forge` runs
the file.

| Module | Takes (current lines) | Approx. lines |
|---|---|---|
| `config.py` | env helpers and the per-phase constant blocks (444-1654, 1655-1832) | 900 → most prose moves to README |
| `cli.py` | resolve_claude, build_argv, argv/model routing, stream parsing, `ActivityRecorder`, `checkpoint_reply`, `read_stream`, and the new job-object process control (1834-2105, 2107-2960, 3679-3787) | 1,300 |
| `jobs.py` | `JobStore` plus a conversation map and session epoch, `public_job`, `run_turn`, queue (3187-3672, 3790-3970) | 800 |
| `files.py` | FileTokens, path discovery, uploads, webui assets. One shared `safe_segment()` for every gate listed above (2961-3185, 3973-4177) | 450 |
| `blender.py` | socket client, live context, snapshot, joint and weights runners. Script bodies move to `blender_scripts/*.py` and are loaded as text (4180-4538, 6144-6337, 6538-6746, 7219-7470) | 900 + scripts |
| `projects.py` | projects, schema, previews, thumbnails, versions, pipeline board, deliverables, task config, journal, refs, workflows, create, attach, library cards (4540-5350, 5661-6143, 6340-6536, 7778-8866) | 2,600 |
| `models.py` | the models row (5351-5658) | 310 |
| `inspect.py` | findings normalizers, authoring tables, direct-tier validation, dirty stages (6750-7105, 7105-7218, 7471-7532) | 700 |
| `http.py` | Handler with a route table of `(method, pattern, handler)` plus decorators for drain, project lookup and Blender error mapping. Health and start-services go here (7566-7775, 8869-11299) | 1,400 → ~900 after dedupe |

Order, one lane each, with the full `assistant/tests` suite green after every step as the gate for
the whole artifact:
1. `cli.py` and `jobs.py` first. That is where findings 2-10 get fixed.
2. `http.py` with the route table, which fixes finding 11 by construction.
3. `files.py`.
4. The rest, mechanically.

The shim's integrity check: the line count of the package plus shim stays within tolerance of the
snapshot, and every name the tests use still resolves.
