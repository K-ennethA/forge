# Forge MCP server

A stdio MCP server that gives Claude Code one tool surface over the Forge backends:

| Backend | Address | Used for |
|---|---|---|
| Blender add-on (`addon/forge/`) | TCP `127.0.0.1:9876` | scene inspection, common mesh ops, mesh loading, generated-mesh import, STL export, imported-model checks/segmentation, RigForge tags/retopo/UV/rig/cloth/animation/Godot export |
| Geometry service (`service/`) | HTTP `127.0.0.1:8765` | PartForge parametric parts (Build123d), print-readiness checks and segmentation |
| meshgen (`meshgen/`) | HTTP `127.0.0.1:8902` | image-to-3D. **Optional**: it needs an 18.5 GB model download, so "not running" is a normal answer and every other tool works without it |

Wire formats are fixed by [`docs/architecture.md`](../docs/architecture.md); this server is
a thin, well-labelled wrapper over them. It holds no state and opens a fresh connection per
call, so backends can start, stop and restart underneath it without a Claude Code restart.

## Setup

`mcp/.venv` is created and installed. To rebuild it from scratch on another machine, from
`mcp/`:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

(If `py` is not on PATH, call the interpreter by its full path — on this machine that is
`%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe`.)

That installs `mcp` and `httpx` and puts `forge_mcp` on the venv's path, which is what makes
`python -m forge_mcp` work from any working directory. The `[dev]` extra adds `pytest`; drop
it for a runtime-only install.

The server is built against the **mcp 2.x** SDK, which renamed `FastMCP` to `MCPServer`
(`mcp.server.mcpserver`) — hence the `mcp>=2,<3` pin. Two consequences worth knowing:

- Only the SDK's `ToolError` has its message returned to the model; every other exception is
  treated as a crash and the caller sees a bare `Error executing tool <name>` while the real
  message goes to stderr. `ForgeError` therefore derives from `ToolError`, which is what
  keeps the messages below actionable. Genuine bugs stay on the crash path on purpose.
- On the wire the tool schema field is `inputSchema`; on the Python model it is
  `input_schema`.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

`tests/` covers path/formatting logic, the NDJSON framing (against an in-process fake socket
server on an ephemeral port), the 63-tool surface and its schemas, the backend-down error
messages, the stdio handshake against a real `python -m forge_mcp` subprocess, and the
`.mcp.json` registration.
`tests/test_workspace.py` does it for the nine Phase 8 tools: what each workspace command
puts on the wire, that every report carries **both** halves of the teaching contract (what
changed and where the switch lives) plus the instruction not to write out the steps, the
motivating case itself (`grid` + all three axes in one call, with the axes on the wire),
the `axes` validation matrix refused before the socket, `mesh_diagnose` rendering a place
against every defect and a short answer on a clean mesh, and `check_my_work` composing its
four calls **in order** — including that a Blender with no viewport degrades to a note
instead of losing the whole check-in. `tests/test_print_readiness.py` adds the Phase 2 tools: mode
normalization, what each tool actually PUTs on the wire, and what its report says — against
a stdlib `http.server` fake on an ephemeral port plus the same fake Blender socket.
`tests/test_rigforge.py` does the same for the Phase 3, 4 and 5 `rigforge_*` tools: the
command name and every parameter of each request, the `faces` / `use_selection` and
`tags` / `use_selection` either-ors, the `actions` `"all"`-or-list forms, per-key keyframe
validation, the `.bvh`/`.fbx` refusal, and each report rendered from a canned,
contract-shaped result — including a warnings-heavy one and a nearly empty one, because the
add-on side is being written in parallel and a thin result must still render.
`tests/test_mesh_input.py` does it for the two Phase 6d tools (`check_model`, `segment_model`):
every parameter each puts on the wire, the report rendered from a canned add-on result —
including a plate that does not fit and a nearly empty result — that a non-watertight refusal
reaches the caller **word for word** with the voxel-remesh fix appended, and that a merely
absent add-on is not dressed up as a mesh problem.
`tests/test_meshgen.py` does it for the two Phase 7 tools (`generate_3d`, `meshgen_status`)
against a scripted fake meshgen and the same fake Blender: the exact `/generate3d` body, that
a file which is not a picture never starts a five-minute job, that the job is followed to the
end and its stage names survive into the report in order, that the import always asks for the
repair, that the verdict comes from `check_model` on what landed, that Blender being down
loses the import but never the `.glb`, and both `/health` renderings (ready, and
models-missing with every file named). Nothing binds or connects to 8902.
`tests/test_new_part.py` covers the two authoring tools: the slug matrix, every path-shaped
name it refuses, that a script the service rejects leaves nothing on disk, the
overwrite/spec matrix, and the one `partforge_open` command that crosses the wire. It
redirects `projects/` to a `tmp_path`, so the real folder is never touched.
`tests/test_flows.py` does the same for the three flow tools: the slug matrix and every
path-shaped name, the validation matrix (unknown command, unknown endpoint, bad `kind`,
non-object `args`, a nested `flow_run`, a parameter with no `value`, a one-step "flow"),
that a refusal writes **nothing**, the one `flow_run` command that crosses the wire with its
slugged name, the per-step report rendering, and that the repo's own
`flows/segment-into-4.json` passes the validation `flow_save` applies. It redirects
`flows/` to a `tmp_path` too. `tests/test_base_shapes.py` covers the three Phase 11 tools:
what each sampler puts on the wire (and that an omitted option is *absent* rather than a
null), that a profile report hands back pasteable control points and says out loud that
nothing was built, that an outline that crosses itself is flagged before `silhouette_part`
refuses it, that `merge_for_print` resolves its pieces the documented way, that its report
states the voxel trade and the hidden-not-deleted rule and always names `check_model` next,
and that `partforge_generate`'s `collection` really reaches `load_mesh` so the component
convention is reachable rather than aspirational. Nothing in the suite needs Blender or the
geometry service, and nothing binds or connects to 9876/8765/8902.

`tests/e2e_new_part.py` is deliberately **not** a pytest module: it is the end-to-end proof,
and it needs the real service on 8765 and launches its own headless Blender (socket port
9885, never 9876). It runs the whole loop for "small magnet holder" — first draft, open,
generate, check FAILS on min_wall, revise with `overwrite=true`, check PASSES — asserts the
artifacts and the scene object, then deletes the project folder it created. Run it by hand:
`.\.venv\Scripts\python.exe tests\e2e_new_part.py`.

### Registering with Claude Code

`.mcp.json` at the repo root is the project-scope registration — Claude Code picks it up
automatically when it is started in the repo (it prompts once to approve a project MCP
server; `/mcp` lists servers and lets you re-approve or inspect one). JSON allows no
comments, hence this section:

```json
{
  "mcpServers": {
    "forge": {
      "command": "C:\\Users\\kenne\\OneDrive\\Desktop\\git\\forge\\mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "forge_mcp"],
      "env": { "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

- The `command` is an **absolute path** to the venv interpreter, which works no matter what
  directory Claude Code was launched from. On another machine (or a different checkout),
  change that one string. The relative form `mcp/.venv/Scripts/python.exe` also works when
  Claude Code is always started from the repo root; absolute is the safer default.
- On macOS/Linux the interpreter is `mcp/.venv/bin/python` instead.
- No `pip install` step is embedded anywhere: if the venv is missing, the server simply
  fails to start and `/mcp` shows it as failed.

Verify with `/mcp` in Claude Code, then ask for `forge_status`.

### Environment overrides

All optional; set them in the `env` block of `.mcp.json` if the defaults do not fit.

| Variable | Default | Meaning |
|---|---|---|
| `FORGE_BLENDER_HOST` / `FORGE_BLENDER_PORT` | `127.0.0.1` / `9876` | Blender add-on socket |
| `FORGE_BLENDER_CONNECT_TIMEOUT` | `2.0` | seconds to wait for the socket to accept |
| `FORGE_BLENDER_READ_TIMEOUT` | `180.0` | seconds to wait for a command's reply (raise for heavy remeshes) |
| `FORGE_SERVICE_URL` | `http://127.0.0.1:8765` | geometry service base URL (overrides host/port) |
| `FORGE_SERVICE_HOST` / `FORGE_SERVICE_PORT` | `127.0.0.1` / `8765` | geometry service address |
| `FORGE_SERVICE_CONNECT_TIMEOUT` | `2.0` | seconds to wait for the HTTP connection |
| `FORGE_SERVICE_READ_TIMEOUT` | `180.0` | seconds to wait for a build/export |
| `FORGE_SERVICE_CHECK_TIMEOUT` | `150.0` | seconds to wait for `/check` (the service allows itself 120) |
| `FORGE_SERVICE_SEGMENT_TIMEOUT` | `330.0` | seconds to wait for `/segment` and `/export_segments` (the service allows itself 300) |
| `FORGE_PRINTER_PATH` | `<repo>/templates/printer.json` | default printer profile for the print-readiness tools |
| `FORGE_PROJECTS_DIR` | `<repo>/projects` | the only folder `partforge_new_part` writes to |
| `FORGE_FLOWS_DIR` | `<repo>/flows` | the only folder `flow_save` writes to, and what `flow_list` reads (the add-on's `forge_flows_dir` preference must agree) |
| `FORGE_FLOW_RUN_TIMEOUT` | `900.0` | seconds to wait for a whole `flow_run` (one flow can hold a 300 s `/segment` plus mesh loading) |
| `FORGE_PREVIEWS_DIR` | `%TEMP%\forge-previews` | where `render_preview` drops the PNGs the model Reads — scratch by design, never the repo or a project |
| `FORGE_PREVIEW_TIMEOUT` | `180.0` | seconds to wait for one `render_preview` (a Workbench render of an ordinary part is well under a second; the budget is for a dense import at 2048 px) |
| `FORGE_MESHGEN_URL` | `http://127.0.0.1:8902` | meshgen base URL (overrides host/port) |
| `FORGE_MESHGEN_HOST` / `FORGE_MESHGEN_PORT` | `127.0.0.1` / `8902` | meshgen address |
| `FORGE_MESHGEN_CONNECT_TIMEOUT` | `2.0` | seconds to wait for meshgen's HTTP connection |
| `FORGE_MESHGEN_READ_TIMEOUT` | `30.0` | seconds for one meshgen call — never the job, which is polled |
| `FORGE_MESHGEN_JOB_TIMEOUT` | `900.0` | seconds `generate_3d(wait=true)` follows a job before handing back the id (a run is ~250–305 s) |
| `FORGE_MESHGEN_POLL_INTERVAL` | `3.0` | seconds between `/job` polls |
| `FORGE_MAX_RESPONSE_BYTES` | `268435456` | refuse to buffer a runaway response |

The two Phase 2 timeouts sit deliberately *above* the service's own budgets
(`FORGE_CHECK_TIMEOUT` / `FORGE_SEGMENT_TIMEOUT` on its side): a client that gives up first
turns a slow-but-working job into a mystery, where letting the service time out produces a
400 that says what went wrong.

## Tool catalog

### Health

| Tool | What it does |
|---|---|
| `forge_status` | Reachability of all three backends in one call, with Blender/build123d versions and meshgen's backend. Never fails; meshgen being down is reported as optional, not as a fault. |
| `blender_ping` | Cheapest check that Blender is up with the add-on server started. |
| `meshgen_status` | meshgen's `/health` (backend, licence, VRAM, queue, any missing model files with their paths and URLs) plus one job's stage when given a `job_id`. Never fails. |

### Blender scene

| Tool | What it does |
|---|---|
| `get_scene_info` | Every object: name, type, location, dimensions, vertex count, modifiers, plus the active object. |
| `execute_blender_python` | Escape hatch: run arbitrary `bpy` code on Blender's main thread, return stdout. |

### Common mesh operations

Each takes an optional `object` name; omitted means Blender's active object.

| Tool | Key params | What it does |
|---|---|---|
| `symmetrize` | `direction` `+X/-X/+Y/-Y/+Z/-Z` | Keeps the named side and mirrors it over the other, destructively. `+X` = the +X half survives. |
| `mirror` | `axis`, `use_clip`, `apply` | Adds a Mirror modifier (duplicates across the axis), optionally applied. |
| `remesh` | `mode` `voxel`/`quad`, `voxel_size`, `target_faces` | Rebuilds topology: uniform watertight voxels, or Quadriflow quads. Returns new counts. |
| `decimate` | `ratio` | Collapse-decimate to a fraction of faces kept (0.5 = half). Returns the new face count. |
| `shade` | `mode` `smooth`/`flat`/`auto`, `angle` | Shading and auto-smooth angle. |
| `apply_transforms` | `location`, `rotation`, `scale` | Bakes the transform into mesh data (all three default to True). |
| `set_origin` | `type` `geometry`/`bottom`/`cursor` | Moves the origin without moving the object. `bottom` for printed parts. |
| `boolean` | `operand`, `operation`, `apply`, `delete_operand` | UNION / DIFFERENCE / INTERSECT against another object. |
| `merge_by_distance` | `distance` | Welds near-coincident vertices. Returns how many were removed. |
| `separate_loose` | — | Splits disconnected shells into separate objects. Returns their names. |

### Object management

| Tool | What it does |
|---|---|
| `select_object` | Select + make active, so later calls can omit `object`. |
| `rename_object` | Rename; returns the final name (Blender may append `.001`). |
| `delete_object` | Delete an object from the scene. |
| `export_stl` | Write objects (or the current selection) to one `.stl`. |

### Reference images (Phase 6c)

| Tool | Key params | What it does |
|---|---|---|
| `load_reference` | `path`, `view` `front`/`side`/`top`, `size_mm`, `name` | Puts the artist's sketch or photo in the viewport as a half-transparent image plane, facing the named orthographic view (front = Numpad 1, side = Numpad 3, top = Numpad 7) and sitting a millimetre behind the origin so it never z-fights the model. |

`size_mm` is the picture's **longer** side in millimetres (default 200); the shorter side
follows the file's own pixel aspect, so a reference is never stretched. `name` defaults to
`Ref-<view>`, and loading the same name again **replaces** that reference rather than
leaving `Ref-front.001` behind. `.png`, `.jpg`, `.jpeg`, `.webp` and `.bmp` only — the same
five the Assistant's attach field and the bridge accept, checked here (exists, is a file,
right extension) before Blender is touched at all.

The object is an **empty**, not geometry: it cannot be exported, printed or booleaned by
accident, and the artist can move, scale or hide it like anything else. It is there to
**measure against, never to trace** — geometry is always built from parameters, which is
what keeps it editable with real numbers (docs/plan.md §3).

### Previews — the model's eyes

| Tool | Key params | What it does |
|---|---|---|
| `render_preview` | `objects`, `view` `iso`/`front`/`side`/`top`, `resolution` (128–2048), `shading` `solid`/`material` | Renders the scene to a PNG and returns the path, so the model can **Read the file and look at what it made**. Nothing is required: no path (it picks a scratch one), no object (it frames every visible mesh). |

### The workspace copilot (Phase 8) — drive their Blender, don't describe it

Seven tools that change what the artist is looking at, what mode they are in and what
brush is in their hand. **Nothing is required on any of them** — a copilot that needs a
form filled in before it will turn the grid on is a tutorial with extra steps.

Every one returns the same two things: **what changed** and **where the switch lives in
Blender's own UI**, with the instruction to pass both on in ONE line and not to write out
the steps. That last part is the whole point: the phase exists because an artist asked to
"enable grid view for x,y,z axis" and got a nine-step tutorial from a program running
inside their Blender.

| tool | key arguments | what it does |
|---|---|---|
| `set_view` | `view` `front`/`back`/`left`/`right`/`side`/`top`/`bottom`/`iso`/`camera`, `ortho` | Turns every open 3D viewport. Axis views are flat by default (the projection you can measure a reference against, and the same three `render_preview` and `load_reference` use); `iso` stays in perspective. |
| `frame_object` | `object`, `all`, `margin` | Zooms onto one object, or everything visible. Never touches the selection. |
| `local_view` | `enable`, `object` | Isolates the selection so the body stops getting in the way of the ear. Naming an object selects it first. |
| `set_shading` | `mode` `solid`/`wireframe`/`material`/`rendered` | The four shading balls. `wireframe` is the one for looking at topology. |
| `set_overlays` | `grid`, `axes`, `wireframe`, `stats`, `overlays`, `origins`, `cursor`, `text`, `face_orientation`, `xray` | **The motivating case:** `set_overlays(grid=True, axes=["x","y","z"])` is the whole of "enable grid view for x,y,z axis". `axes` takes a list, `"all"`, `true` or `[]`, and the list is authoritative. Anything not named is left as the artist had it. |
| `set_mode` | `mode` `object`/`edit`/`sculpt`/`vertex_paint`/`weight_paint`/`texture_paint`/`pose`, `object` | Checks the object type supports the mode first, then selects, activates and switches. |
| `sculpt_brush` | `brush`, `size`, `strength`, `symmetry_x/y/z`, `dyntopo`, `object` | Picks the brush and sets it up, entering Sculpt Mode on the way. A miss names the closest brush that exists. The line the docstring holds: **technique is theirs, selection and settings are yours.** |

### Buddy mode (Phase 8) — a teacher's eyes on the work in progress

| tool | key arguments | what it does |
|---|---|---|
| `mesh_diagnose` | `object`, `examples` (1–25), `density_ratio` | Clipping (self-intersections), unsealed edges, zero-area faces, density hotspots and starved regions, ngons, loose geometry, scale anomalies — **each with a location in millimetres**, so a critique can say where. Renders worst-first with the instruction to name at most three. Read-only, under a second on a 200 000-face sculpt. |
| `check_my_work` | `object`, `resolution` | Composes the whole gather in one call: `capture_viewport` (what they are looking at — their angle, their shading, their overlays), `render_preview` (a clean 3/4 clay render), `mesh_diagnose` and `get_scene_info`. Hands back both image paths with **READ BOTH FILES NOW** and the teacher framing. A piece that cannot be gathered — a headless Blender has no viewport to photograph — degrades to a `could not gather:` note rather than losing the check-in. |

The add-on's **Check my work** button gathers exactly the same four things and sends them
through the assistant bridge instead; `capture_viewport` is a socket command on the
add-on side, reached here through `check_my_work` rather than as a tool of its own.

This is the only tool whose report exists to trigger another tool call. It names the path on
its own line, says `READ THAT FILE NOW`, and then names the four things to look for —
**density, proportions, silhouette, softness** — because a preview nobody looked at is the
same blind design with an extra tool call in front of it. `partforge_check` answers *"can
this be printed"*; it says nothing about whether the thing resembles what was asked for, and
a part can pass every check and still come out stiff, sparse and flat next to the reference.

- **Paths are scratch and never reused.** They land in `FORGE_PREVIEWS_DIR` (default
  `%TEMP%\forge-previews`) as `preview-001-iso-<object>.png`, `preview-002-front.png` and so
  on — a second preview is a *comparison* with the first, and comparing needs both files to
  still exist. They are how the model sees, not artefacts the artist keeps, so they never go
  into the repo or a project.
- **`front` / `side` / `top` are `load_reference`'s three exactly**, so a `front` render and
  a `front` reference are the same projection and can be held up against each other 1:1.
  `iso` (the default) is a 3/4 orbit, which is where a silhouette shows itself.
- `render_preview` is a legal **flow** step, so a flow can end by leaving a picture on disk.

The add-on side does the work — an orthographic camera fitted to the target bounds, Workbench
clay, and every borrowed setting restored in a `finally`. See
[`addon/README.md`](../addon/README.md) for the framing maths and the state-restoration
contract.

### PartForge

| Tool | What it does |
|---|---|
| `partforge_parse_params` | Reads a script's `PARAMS` block — names, values, units, ranges, descriptions. No geometry built. |
| `partforge_generate` | Builds the part **and** loads the mesh into Blender in one call (`load_mesh`, `replace=true`). Reports verts/faces/bbox/watertight. `name` and `collection` are how a multi-part design lands as a component tree: one collection named after the project, the core named after the project, each proposal `<project>-<component>` (see Phase 11 below). |
| `partforge_export` | Rebuilds and writes STL / STEP / 3MF straight from the solid. |

### PartForge authoring — how the assistant makes new parts

The three tools above all assume a script already exists. These two are how one comes into
being: the artist asks for "a small magnet holder", nothing in `projects/` is a magnet
holder, so the model **writes** the part.

| Tool | Key params | What it does |
|---|---|---|
| `partforge_new_part` | `name`, `script_source`, `overwrite`, `components` | Validates the script through the service's `/parse_params` and — only then — writes it to `projects/<slug>/part.py`, plus a minimal `spec.json` beside it. Returns the path and the parsed parameter table. `components` (`["collar", "ear-l"]`) records the component tree in the spec — see Phase 11 below. |
| `partforge_open_in_panel` | `script_path` | Points Blender's Forge panel at that script and rebuilds its sliders (the `partforge_open` socket command, the panel's own Load Script path). Builds nothing — generate next. |

The loop the assistant runs, and the reason each step is there:

```text
read docs/part-authoring.md          # the rulebook: printability + the forge_lib catalog
partforge_new_part(name="small magnet holder", script_source="...")
partforge_open_in_panel(script_path="projects/small-magnet-holder/part.py")
partforge_generate(script_path="projects/small-magnet-holder/part.py")
partforge_check(script_path="projects/small-magnet-holder/part.py")
# min_wall FAILED -> revise the script and go again, up to 3 rounds:
partforge_new_part(name="small magnet holder", script_source="...", overwrite=True)
partforge_check(script_path="projects/small-magnet-holder/part.py")
```

Five things are fixed by the tools rather than left to the model:

- **`name` is a name, not a path.** It is slugged (`"a small Magnet Holder!"` →
  `small-magnet-holder`) and anything path-shaped — a separator, `..`, a drive letter, a
  `~`/`%VAR%` — is refused outright. Nothing is ever written outside `projects/`, and the
  slug rule is checked twice (once on the name, once on the resolved path).
- **Validated before written.** `/parse_params` runs first; a bad `PARAMS` block or a script
  that will not import comes back as the service's own message and **no file is created**, so
  `projects/` never fills with drafts that do not run. A failed *revision* leaves the working
  script exactly as it was.
- **`overwrite=true` is the revision path.** Same tool, same validation — that is what the
  self-correction loop calls after a check fails. Without it, an existing `part.py` is
  refused with a message that says so.
- **`spec.json` is written once.** Name, description placeholder, the parameters mirrored
  from the schema the service just resolved, and a `print` section pointing at
  `templates/printer.json` (shape per `templates/spec.json`). An existing spec is **never**
  overwritten — that file is the artist's to edit.
- **The panel and the tools agree on the object.** `partforge_open_in_panel` derives the same
  object name `partforge_generate` uses (the script's stem, or its folder when the file is
  generically named `part.py`), so the artist's Regenerate button rebuilds the object the
  assistant made rather than a second one. It also clears the previous part's check rows —
  a stale FAIL in the panel is worse than an empty one.

### PartForge print readiness (Phase 2)

The pipeline is *check → segment → load or export*. All four read the same printer profile
(`printer_path`, default the repo's `templates/printer.json`; missing = the service's
built-in Elegoo Centauri Carbon defaults) and take the same `overrides` as
`partforge_generate`.

| Tool | What it does |
|---|---|
| `partforge_check` | Can this be printed? Bed fit, wall thickness, overhangs, watertightness — one verdict, then a line per check with the numbers. When `bed_fit` fails it prints the service's suggested cut mode, which goes straight into the three tools below as `mode`. |
| `partforge_segment` | Plans the cut: segment names, kinds, oriented bounding boxes, the joints and the packed plate. `include_mesh=false` on the wire — this is planning, and segment meshes are large. |
| `partforge_load_segments` | The same cut, with the meshes, loaded into Blender as one object per segment, positioned and spun exactly where the plate packing put them. Shows the user the print plate rather than the assembled part. |
| `partforge_export_segments` | Writes one file per segment and per printed pin (oriented, centred, on Z=0) plus `<basename>_plate.3mf`, and lists every file with its size on disk. |

`mode` is deliberately forgiving: `"auto"`, an integer (radial wedge count), a list of Z
heights, `"30, 60"`, or `partforge_check`'s `{"radial": 4}` object copied verbatim.
`joint_type` is `dovetail` / `pin` / `magnet` / `none`, and `joint_tolerance` overrides the
printer profile's `press_fit` / `magnet_pocket_extra` when given.

### Imported models (Phase 6d) — the downloaded-STL pipeline

The four tools above all need a **PARAMS script**. These two need only an object that is
already in the Blender scene, which is what makes them the answer to "I downloaded this
dragon off Thingiverse, will it print?" and "cut it up, it is too tall for my bed". The
add-on takes the object's evaluated mesh, scales scene metres to millimetres and posts it to
the service's `/check_mesh` / `/segment_mesh` — the same checkers and the same cutter, fed
triangles instead of a solid.

| Tool | Key params | What it does |
|---|---|---|
| `check_model` | `object`, `printer_path` | Bed fit, wall thickness, overhangs, watertightness on an imported mesh, with the rows also written into the panel's Print Checks box. When `bed_fit` fails it prints the suggested cut mode, which goes straight into `segment_model` as `mode`. |
| `segment_model` | `object`, `mode`, `joint_type`, `joint_tolerance`, `collection`, `printer_path` | Cuts that mesh into printable, joinable pieces, packs them onto one plate **and** loads the pieces back into Blender where the packing put them. Names the objects that landed and warns when the plate does not fit. |

Both take an optional `object` (omitted = the active object) and the same `printer_path` as
the Phase 2 tools, and `mode` / `joint_type` / `joint_tolerance` mean exactly what they mean
on `partforge_segment` — including `check_model`'s own `{"planar": [120.0]}` copied verbatim.

Two differences from the PARAMS path are worth saying to the artist out loud:

- **There is no B-Rep.** Solid validity is never reported (the report says `n/a (no B-Rep)`
  rather than a bare `None`); `watertight` is the triangle mesh's own closedness.
- **A model with holes is refused, not checked.** The service answers "repair first (voxel
  remesh)", which arrives here as a socket error — so both tools surface that sentence
  **verbatim** and append the fix: `remesh(mode="voxel")` on the object (the panel's Model
  box has a **Voxel Repair** button that does the same), then ask again. That is a
  one-command repair, not a dead end, and it is why the refusal is worth reading rather than
  retrying.

Both commands also get their own socket budget: they proxy a `/check_mesh` (120 s) or
`/segment_mesh` (300 s) call through Blender, so the read timeout is the larger of
`FORGE_BLENDER_READ_TIMEOUT` and the matching service budget rather than the per-command
default.

```text
# the artist imported dragon.stl themselves (File > Import), then:
check_model(object="dragon_bust")
# bed_fit FAILED, and the report hands back mode = {"planar": [120.0]}
segment_model(object="dragon_bust", mode={"planar": [120.0]}, collection="Pieces")

# if instead the check is refused as not watertight:
remesh(mode="voxel", object="dragon_bust")   # or the panel's Voxel Repair button
check_model(object="dragon_bust")
```

### Base shapes and merge-for-print (Phase 11)

When 100% likeness is not achievable, the deliverable is **structure**: a dimensioned,
sculptable base shape with the right proportions. There are three doors into one — described,
shown in a picture, or **drawn** — and these two read-only tools are the drawn one.

| Tool | Key params | What it does |
|---|---|---|
| `profile_from_curve` | `curve_object`, `points` (5–10, default 7), `close_bottom` | Samples a curve the artist drew into the `(radius, z)` control points `forge_lib.soft_body` takes. Nothing is built. |
| `outline_from_curve` | `curve_object`, `points` (6–16, default 12), `recenter` | Samples a **closed** drawn curve into the `[x, y]` outline `forge_lib.silhouette_part` extrudes. Nothing is built. |
| `merge_for_print` | `objects`, `collection`, `voxel_size_mm`, `name`, `keep_originals` | Joins the pieces the artist kept and voxel-remeshes them into ONE watertight shell for the slicer. Originals hidden, not deleted. |

- **The points come back; the geometry does not.** Both samplers are measurements. You write
  the points into a PARAMS script with `partforge_new_part`, which is what keeps a drawn
  shape parametric — sliders, regeneration, a part that can still change. Handing back a
  mesh traced off the curve throws away the only thing that made the door worth opening.
  The reports print the points in the shape they get pasted in.
- **What to tell the artist**: Numpad 1 for the front view, **Add ▸ Curve ▸ Bezier**, draw
  the right-hand edge of the silhouette going up with the model's centre line on the origin.
  For an ear, a fin or a tail it is a closed loop instead (`A`, then `Alt+C`).
- **`merge_for_print`'s voxel size is the whole argument.** The default is half the printer's
  nozzle — 0.2 mm on a 0.4 mm nozzle — because two voxels per bead keeps every detail the
  printer could lay down and spends nothing on detail it could not; finer costs file size
  quadratically for no change on the plate. On a big model the add-on coarsens it to stay
  under a million faces and says so, and the report tells you to pass that trade on rather
  than hide it.
- **The pieces**: name them in `objects`, or name a `collection` (every VISIBLE mesh in it),
  or name nothing and merge the selection. That is what makes the component tree work —
  scrapping a proposal is `delete_object` or the eye icon, and "merge what's left" is the
  same call as before.
- **`check_model` always follows a merge.** The report says so, because a merged shell is a
  new mesh nobody has print-checked; when a check fails, `mesh_diagnose` gives the thin
  places in millimetres so the artist knows where to thicken.
- **spec.json records the tree.** `partforge_new_part(..., components=["collar", "ear-l"])`
  writes a `components` block — `{"collection": <slug>, "core": <slug>, "proposals":
  ["<slug>-collar", ...]}` — so a session that starts tomorrow knows which object is the
  dimensioned core and which ones the artist may scrap. Passing a full name
  (`"gecko-bowl-collar"`) records the same thing; the prefix is never doubled. On a revision
  the block is **merged** into the existing spec and nothing else in that file is touched —
  a spec that cannot be parsed is left exactly as it is, with a sentence in the report.
- **All three are legal flow steps**, and the two ends of the workflow ship as saved flows:
  `flows/sculpt-ready.json` (remesh → Sculpt Mode → brush, the handoff to their stylus) and
  `flows/merge-and-check.json` (merge, then print-check). `flow_run(name="sculpt-ready")`
  beats improvising the same three calls, per the flow law above.

```text
# the artist drew the silhouette and called it VaseProfile
profile_from_curve(curve_object="VaseProfile", points=7)
partforge_new_part(name="gecko-bowl", script_source=...)   # the points, in a PARAMS block
partforge_generate(...); partforge_check(...); render_preview()

# they sculpted on it, scrapped the collar, and are done
delete_object(name="gecko-bowl-collar")
merge_for_print(collection="gecko-bowl")
check_model(object="gecko-bowl-merged")
```

### Picture to 3D (Phase 7) — meshgen

Two tools over the meshgen service on `127.0.0.1:8902`. `generate_3d` is the whole job in
one call: the picture goes to the AI model, the finished `.glb` is imported into Blender
through the add-on's `import_generated` command, **voxel-repaired on the way in**, and
print-checked — so what comes back is an object in the scene plus a verdict, not a file
path.

| Tool | Key params | What it does |
|---|---|---|
| `generate_3d` | `image_path`, `backend`, `wait` | Picture → mesh → repaired object in the scene → print verdict. `wait=false` returns the job id immediately instead. |
| `meshgen_status` | `job_id` | Is the service up, which backend, what is missing, and which stage a job is on. |

Four facts the report always carries, because each one is a promise an artist would
otherwise be let down by:

- **It takes about five minutes** (measured on the reference RTX 5070: 304 s trellis2,
  249 s pixal3d) and one job runs at a time. Say so *before* starting.
- **`progress` is per stage, not per job.** It resets every time the pipeline moves on, so
  the report relays the stage NAMES in order (`Trellis2UpsampleStage -> RemeshMesh ->
  UnwrapMesh`) and never quotes a percentage as "done".
- **The repair is not optional.** Raw image-to-3D output is never manifold — paper-thin
  walls, boundary edges, inconsistent winding — so `import_generated` voxel-remeshes it at
  an adaptive size before anything downstream sees it.
- **The print verdict usually fails** — on `min_wall` at a sensible scale, on `bed_fit` at
  the metre-ish scale these models come out at. That is the correct diagnosis of a generated
  mesh, not a broken tool, and the report says so rather than burying it.

The object is named after the picture (`gecko.png` → `gecko`), because a `.glb` names its
own mesh `Mesh_0` and the artist thinks in the file they chose.

When to reach for which: **parametric** (`partforge_new_part`) for anything functional,
dimensioned or printed to fit; **generate_3d** for organic, stylised one-offs where looking
right beats measuring right. A generated mesh has no crisp faces, no exact millimetres and
no fine detail, and nothing in a picture says how big the thing is — scale is a decision the
artist still has to make.

Blender being down does not lose the run: the `.glb` is on disk and the report says so with
the path, ready for `import_generated` once Blender is up.

```text
# the whole job, one call (five minutes)
generate_3d(image_path=r"C:\Users\me\Pictures\gecko.png")

# or start it and get on with something else
generate_3d(image_path=r"C:\...\gecko.png", wait=False)
meshgen_status(job_id="11111111-2222-...")     # stage: RemeshMesh, 20% through THAT stage

# is it even installed on this machine?
meshgen_status()      # names every missing weight file, its path and its URL
```

### RigForge (Phase 3)

Getting a sculpt ready to animate: label the parts, write that down, rebuild the topology,
unwrap. Each takes an optional `object` name; omitted means the active object.

| Tool | Key params | What it does |
|---|---|---|
| `rigforge_status` | — | One-call overview: vertex/face counts, every tag with its size, whether a `_retopo` / `_lod*` sibling and a metarig/rig exist, the rig's action library once there is a rig — and the one next call in the chain. Start here. |
| `rigforge_list_tags` | — | The body-part tags on a mesh with their vertex/face counts. |
| `rigforge_tag` | `tag`, `faces` \| `use_selection`, `replace` | Label faces as a body part ("Head", "Arm.L", "Ear.R"). Exactly one of `faces` / `use_selection`. |
| `rigforge_untag` | `tag`, `faces` \| `use_selection` | Remove faces from a tag, or omit both to delete the tag entirely. Never touches geometry. |
| `rigforge_manifest` | `action` `save`/`load`/`get`, `path`, `archetype`, `motion_notes` | Read or write the character's `character.json` (see `templates/`). |
| `rigforge_retopo` | `platform` `desktop`/`mobile`, `target_faces`, `lods`, `bake_normals`, `bake_resolution`, `keep_original` | Voxel remesh → Quadriflow → shrinkwrap → tag transfer, plus optional normal bake and LODs. Tables every object it created with its face count. |
| `rigforge_auto_uv` | `seams_from_tags`, `margin`, `angle_limit` | Seams at tag boundaries, unwrap, pack. Reports islands and UV coverage. |

Tags are vertex groups prefixed `tag_` on the object; the add-on owns that prefix, so pass
the bare part name (`"Head"` — `"tag_Head"` is accepted and stripped). The face budget comes
from `platform` — desktop 15000, mobile 5000, matching `templates/character.json` — unless
`target_faces` overrides it.

### RigForge rig and Godot export (Phase 4)

Where Phase 3 stops (a clean, tagged, unwrapped mesh), these four take it to a rigged
character in Godot. `rigforge_metarig` and `rigforge_weights` take the usual optional
`object`; the other two name the rig instead.

| Tool | Key params | What it does |
|---|---|---|
| `rigforge_metarig` | `archetype` `auto`/`biped`/`quadruped`/`custom`, `modules` | Places and scales a metarig from the tag landmarks (head top, chin, shoulder, elbow, wrist, hip, knee, ankle). Reports the bone count and which bones each tag drove. |
| `rigforge_generate_rig` | `metarig`, `mesh`, `parent_with_weights`, `cleanup` | Rigify generate → parent with automatic weights → per-tag cleanup rules → normalize. Reports the rig, what got skinned and the cleanup counts. |
| `rigforge_weights` | `action` `report`/`cleanup`/`normalize`, `max_influences` | Reads or repairs the skinning: influences over the limit, unnormalized or unweighted vertices, weights that cross a tag boundary. |
| `rigforge_export_godot` | `path`, `rig`, `meshes`, `actions`, `root_motion`, `deform_only`, `godot_import_script` | Bakes the actions onto the deform bones, strips the control bones, writes the glTF plus its Godot import helper, and lists every file with its size. |

`modules` crosses the wire verbatim (`[{"kind": "tail", "tag": "Tail"}]`) because the add-on
owns that vocabulary; spring/jiggle chains come from the manifest's `motion_notes`, so write
those with `rigforge_manifest` rather than looking for a parameter here. `actions` is `"all"`
(default), a list, or a comma-separated string. `max_influences` is 1..8 — Godot's own limits
are 4 and 8 — and is sent for `report` too, as the limit the report measures against.
`deform_only` and `godot_import_script` default to true; a `deform_only=false` export says
`CONTROL BONES KEPT` in its summary, because that is a debugging export, not a shipping one.
Every one of the four relays the add-on's `warnings` as its own block, ahead of the tables.

### RigForge cloth and animation (Phase 5)

The last four. `rigforge_cloth` is optional (not every character wears something); the other
three are how a rigged mesh gets motion — either described, or captured.

| Tool | Key params | What it does |
|---|---|---|
| `rigforge_cloth` | `tags` \| `use_selection`, `preset`, `output`, `name`, `offset_mm`, `thickness_mm`, `frames`, `collision` | Duplicates the tagged faces, offsets them off the skin, solidifies and (for a simulated output) runs the cloth sim against the body. Reports the garment, how it is driven and any shape keys. |
| `rigforge_action` | `action` `new`/`list`/`delete`/`duplicate`/`rename`/`push_nla`, `name`, `source`, `rig`, `loop` | Manages the Godot action library — the clips themselves. `list` tables every action with its loop badge and frame range. |
| `rigforge_keyframe` | `keys`, `action`, `rig`, `interpolation`, `clear` | **The described-motion tool.** Batch-keys control bones: sketch the pose at a few frames and let Bezier do the rest. Reports keys set and the frame span. |
| `rigforge_retarget` | `source_path`, `action_name`, `target_rig`, `mapping`, `loop`, `scale` | Imports a `.bvh`/`.fbx` the **user** supplies, maps its bones onto the rig, bakes an action and deletes the import. Reports the unmapped bones loudly — that list is the whole story of a retarget. |

`output` is the cloth choice that matters: `skin_tight` (default) copies the body's weights
and never simulates — cheap, deterministic, right for most game characters; `shapekeys` bakes
the settled sim into shape key(s); `bones` is **v1-degraded**, so the add-on may warn and fall
back, and the report's WARNINGS block is what says which. `preset` is `cotton` / `leather` /
`heavy`. `offset_mm`, `thickness_mm`, `frames` and `name` only go on the wire when given —
omitted means the add-on's own default — and `frames` with `skin_tight` is an error, because
nothing simulates there.

`rigforge_keyframe`'s `keys` are `{"bone", "frame", ...}` objects carrying at least one of
`rotation_euler_deg` (degrees), `location` (metres) or `scale`; a key missing its bone, its
frame or every channel is refused here rather than reaching Blender as a silent no-op. Bone
names are the **control** rig's (`hand_ik.L`), not the `DEF-` deform bones. Entry order and
unknown fields are preserved, the way `modules` entries are.

`loop=true` marks a looping clip and the add-on enforces the `-loop` suffix Godot's importer
reads; names cross the wire as typed and the report shows whatever the add-on settled on.
`mapping` is `"auto"` or `{clip_bone: rig_bone}` for only the bones the heuristics get wrong;
`scale` is `"auto"` or a positive multiplier. A `source_path` that is not `.bvh`/`.fbx` is
refused rather than corrected — unlike an export path, that extension describes a file that
already exists, and nothing is ever downloaded.

**A typical run — tag, retopo, unwrap, rig, dress, animate, export.** The tagging step is two
calls whenever the user describes geometry by shape rather than by index:

```text
# "the two lumps on top of the head are its ears"
execute_blender_python(code="""
import bpy
mesh = bpy.data.objects['goblin'].data
for poly in mesh.polygons:
    c = poly.center
    poly.select = c.z > 1.55 and abs(c.x) > 0.06
print(sum(1 for p in mesh.polygons if p.select), 'faces selected')
""")
rigforge_tag(tag="Ear.L", use_selection=True, object="goblin")

# ... one call per part, then check the labelling took
rigforge_status(object="goblin")

# rebuild the topology for a phone target, with the sculpt detail baked in
rigforge_retopo(platform="mobile", lods=2, bake_normals=True, object="goblin")

# unwrap the RETOPO mesh, cutting seams where one tag meets the next
rigforge_auto_uv(object="goblin_retopo")

# write it all down — the motion notes are what flags the ear chains for jiggle later
rigforge_manifest(action="save", path="projects/goblin/character.json",
                  archetype="biped", motion_notes="ears are floppy and lag behind the head",
                  object="goblin")

# place the metarig on the RETOPO mesh, from the tag landmarks, then look at it
rigforge_metarig(archetype="biped", modules=[{"kind": "chain", "tag": "Ear.L"},
                                             {"kind": "chain", "tag": "Ear.R"}],
                 object="goblin_retopo")

# Rigify generate + automatic weights + per-tag cleanup, then check the skinning
rigforge_generate_rig(metarig="goblin_metarig", mesh="goblin_retopo")
rigforge_weights(action="report", max_influences=4, object="goblin_retopo")
rigforge_weights(action="cleanup", max_influences=4, object="goblin_retopo")

# give it a tunic: the torso and arm tags, cotton, moving with the body
rigforge_cloth(tags=["Torso", "Arm.L", "Arm.R"], preset="cotton", output="skin_tight",
               name="goblin_tunic", object="goblin_retopo")

# open a looping clip, then describe the idle in four poses and let Bezier do the rest
rigforge_action(action="new", name="idle-loop", loop=True, rig="goblin_rig")
rigforge_keyframe(rig="goblin_rig", action="idle-loop", clear=True, keys=[
    # breathe: chest up by frame 24, back down by 48, and frame 1 == frame 48 so it cycles
    {"bone": "spine_fk.002", "frame": 1,  "rotation_euler_deg": [0, 0, 0]},
    {"bone": "spine_fk.002", "frame": 24, "rotation_euler_deg": [-3, 0, 0]},
    {"bone": "spine_fk.002", "frame": 48, "rotation_euler_deg": [0, 0, 0]},
    # the head drifts a little off the beat, which is what stops it reading as a machine
    {"bone": "head",         "frame": 1,  "rotation_euler_deg": [0, 0, 2]},
    {"bone": "head",         "frame": 30, "rotation_euler_deg": [0, 0, -2]},
    {"bone": "head",         "frame": 48, "rotation_euler_deg": [0, 0, 2]},
])

# stash it so the next clip starts clean, then check the library
rigforge_action(action="push_nla", name="idle-loop", rig="goblin_rig")
rigforge_action(action="list", rig="goblin_rig")

# a captured clip the USER already has on disk, mapped by name, as a second action
rigforge_retarget(source_path="C:/mocap/run.bvh", target_rig="goblin_rig",
                  action_name="run-loop", loop=True)   # read the UNMAPPED list

# bake onto the deform bones, strip the controls, write the glTF for Godot
rigforge_export_godot(path="projects/goblin/godot/goblin.glb", rig="goblin_rig",
                      meshes=["goblin_retopo", "goblin_lod1", "goblin_tunic"],
                      actions="all")

# and at any point: where are we, and what is the next call?
rigforge_status(object="goblin")
```

Six rules worth stating up front, because each one is an error rather than a guess:

- `faces` and `use_selection` are alternatives, and so are `tags` and `use_selection` on
  `rigforge_cloth`. Passing both is refused (they name different geometry); passing neither is
  refused with a pointer back to the two-call flow above.
- `path` is only meaningful for `action` `save`/`load`. With `get` it is an error, not a
  silent no-op. `frames` is only meaningful when a cloth sim runs; with `skin_tight` it is the
  same kind of error.
- `bake_resolution` only goes on the wire when `bake_normals` is true, the same way
  `remesh`'s `voxel_size` is only sent in voxel mode — and `rigforge_action` only sends `loop`
  for the actions that create or name a clip.
- `max_influences` outside 1..8 and a `modules` entry that is not an object are refused
  before the socket, with the shape that would have worked.
- `name` and `source` mean different things per `rigforge_action` action, so one that cannot
  apply (a `source` on a `delete`, a `name` on a `list`) is refused with what each one
  actually means for that action.
- A keyframe missing its `bone`, its `frame` or every channel is refused by index
  (`keys[3] ('hand_ik.L' at frame 12) sets no channel`), because the alternative is Blender
  cheerfully keying nothing.

### Flows (Phase 6b) — the tools that stop you redoing work

A **flow** is a job someone worked out once, saved as a named, parameterised sequence of
Forge operations in `flows/*.json` that replays with **no model in the loop**. Three tools,
and the order they are used in is the point: *look* before improvising, *run* what is there,
*save* what you just worked out.

| Tool | Key params | What it does |
|---|---|---|
| `flow_list` | — | Every saved flow with its description, parameters (defaults and units) and labelled steps. Reads `flows/` straight off disk, so it answers with Blender closed. **Call this before improvising any multi-step job.** |
| `flow_run` | `name`, `params` | Replays one flow through Blender's `flow_run` command. Renders a line per step (`[ok  ]` / `[FAIL]`, the label, the kind and op, a one-line brief of what it produced). `params` overrides declared defaults only — an undeclared name is an error listing what the flow takes. |
| `flow_save` | `name`, `description`, `steps`, `params`, `overwrite` | Writes `flows/<slug>.json` and nothing else. Validates first: every `op` against the real command/endpoint set, every argument JSON-serialisable, every parameter carrying a `value`. |

`flow_save` is the second and last tool in this server that writes, and it obeys the same
rules as `partforge_new_part`: the name is slugged (`"segment into 4"` →
`flows/segment-into-4.json`), anything path-shaped is **refused rather than cleaned**, and a
refusal leaves nothing on disk. Two additional refusals are specific to flows:

- **a one-step flow** — that is just the tool call, and a folder of one-step flows is worse
  than an empty one;
- **`flow_run` or `flow_list` as a step** — flows do not nest, so what one does stays
  readable in one file.

A step is `{"kind": "blender"|"service", "op": <socket command | endpoint>, "args": {...},
"label": "what this step does"}`. Label every step: the labels are what the artist reads in
the panel and what a failure is reported against. `args` may carry `{{placeholders}}` —
`"{{wedges}}"` as a whole value keeps its type (the number `4`), embedded in a longer string
it is text substitution, and `"{{steps.0.result.segments}}"` reads an earlier step's result
by dotted path (lookup only, no expressions). In a **service** step, `script_path` and
`printer_path` are read off disk by the add-on and become `script`/`printer`; left `""` they
mean "whatever the PartForge panel is pointed at", which is what lets one saved flow serve
every part. The full format is `flows/README.md`.

```text
# before working anything out
flow_list()

# there is already one for this
flow_run(name="segment-into-4", params={"wedges": 6})

# and after finishing something repeatable that was NOT there
flow_save(
  name="check then export segments",
  description="Check the part, cut it into printable pieces and write them to exports/.",
  params={"wedges": {"value": 4, "unit": "count", "description": "How many wedges"}},
  steps=[
    {"kind": "service", "op": "/check", "label": "Check it prints",
     "args": {"script_path": ""}},
    {"kind": "service", "op": "/export_segments", "label": "Cut and write the files",
     "args": {"script_path": "", "mode": {"radial": "{{wedges}}"},
              "directory": "projects/dog-bowl-holder/exports"}},
  ])
```

`flow_run` gets its own socket budget (`FORGE_FLOW_RUN_TIMEOUT`, 900 s) because one flow can
contain a 300-second `/segment` plus the mesh loading after it.

## Troubleshooting

**"Blender is not running or the Forge add-on server is stopped…"**
Nothing is listening on `127.0.0.1:9876`. Blender is closed, the add-on is disabled, or the
server was never started from the Forge panel. Only the Blender tools are affected —
`partforge_export` and `partforge_parse_params` still work, and `partforge_generate` still
builds the part and reports its stats, it just cannot show it in the viewport.

**"The Forge geometry service is not running…"**
Nothing is answering on `127.0.0.1:8765`. Start the service from `service/`. Only the
`partforge_*` tools are affected; Blender tools are unaffected.

**"Blender rejected '<command>': …"**
Blender is up and answered `status: "error"` — the message is the add-on's own. Usually a
bad object name, wrong mode, or an operator that needs a different context. Run
`get_scene_info` to check names.

**"Geometry service error (HTTP 400) …"**
The script or the overrides are bad: a syntax error in the script, a missing `PARAMS`
entry, a value outside `min`/`max`, or a `build()` that raised. The tail of the service
traceback is included in the message.

**"Blender did not answer within 180s"**
A heavy operation (Quadriflow remesh, a boolean on a dense sculpt) is still running on
Blender's main thread — Blender will look frozen until it finishes. Wait, then re-check
with `get_scene_info`; raise `FORGE_BLENDER_READ_TIMEOUT` if this is routine.

**The server does not appear in `/mcp`**
The venv or the `-e .` install is missing, or the `command` path in `.mcp.json` is wrong for
this machine. Test the same command by hand:
`C:\...\forge\mcp\.venv\Scripts\python.exe -m forge_mcp` — it should sit silently waiting on
stdin rather than exiting with a traceback (Ctrl+C to quit).

## Design notes

- **One connection per call.** No pooling, no persistent socket: a restarted Blender or
  service is picked up on the next tool call.
- **Meshes never reach the model.** `partforge_generate` and `partforge_load_segments` move
  the vertex/face arrays straight from the service into Blender and return only stats;
  `partforge_segment` does not ask for them at all. `check_model` and `segment_model` are the
  same rule from the other end: the triangles go Blender → service → Blender and this server
  only ever sees the verdict, the piece sizes and the object names.
- **Segments load in one round trip.** `partforge_load_segments` uses the add-on's
  `load_meshes` command (the plural of `load_mesh`, an additive protocol extension) rather
  than N calls, and forwards each segment's `plate.items` entry verbatim so the add-on —
  which has the geometry — decides where the object lands. See `addon/README.md`.
- **Millimetres vs metres.** The service works in mm and `load_mesh` receives mm; the add-on
  scales by 0.001 on import. Blender-side tools (`voxel_size`, `merge_by_distance`) are in
  Blender units (metres).
- **Windows paths.** Every path argument is expanded (`~`, `%VARS%`), normalized and made
  absolute; missing export directories are created; export extensions are corrected to match
  the requested format. `rigforge_export_godot` is the one place two extensions are both
  right: `.gltf` is honoured when asked for, anything else (including no suffix) becomes
  `.glb`, the single-file form Godot prefers. An **input** path is the opposite case:
  `rigforge_retarget` refuses anything but `.bvh`/`.fbx` rather than rewriting the suffix,
  because that extension describes a file the user already has.
- **The status chain is three-valued about actions.** `rigforge_status` asks the rig for its
  action library, but only once a rig exists, and only best-effort: an add-on without the
  Phase 5 commands reports `actions: unavailable`, which is *unknown*, not empty — so the
  next-step nudge stays on `rigforge_export_godot` rather than inventing an animation step.
- **Ambiguity is an error, not a default.** Where two arguments could both apply
  (`faces` and `use_selection`) or one cannot apply at all (`path` with `action="get"`), the
  tool refuses and says why. A silent precedence rule here tags the wrong geometry or writes
  nothing, and neither failure is visible in the report.
