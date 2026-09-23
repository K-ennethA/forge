# Forge environment registry

Every `FORGE_*` variable this repo actually reads, in one place.

**Why it exists.** A diverged environment is invisible. Two machines running the
same commit, one with `FORGE_PROJECTS_DIR` pointed somewhere else, behave
differently in a way no log line explains â€” and the variable that did it was
documented, if at all, in the README of whichever component happened to read it.
Six READMEs is the same as none: nobody greps six files before believing a bug.

**It cannot rot.** `assistant/tests/test_bridge.py` walks every non-test `.py`
file in the repo, collects every `FORGE_*` string literal, and diffs that set
against the first column of the table below. A new variable that ships
undocumented fails that test, and so does a line here for a variable nobody
reads any more.

**Who echoes what.** The assistant bridge's `GET /health` lists, under
`config.env_set`, the `FORGE_*` names that are **set** in its own environment â€”
names only, never values, because this endpoint is readable by the browser and a
value can be a path the artist would not choose to publish. The handful of
values that actually matter for diagnosing a diverged process are echoed
*resolved* in the same `config` block (`projects_dir`, `models_dirs`, `model`,
`timeout_s`, `blender`, `cwd`, `uploads_dir`, `previews_dir`, `thumbs_dir`,
`services`) â€” resolved is the useful form, since the raw string tells you
nothing about what defaults, `abspath` and `meshgen/config.json` did to it.

**Components.** `bridge` = `assistant/bridge.py`; `service` = the geometry
service (`service/`); `meshgen` = the image-to-3D service (`meshgen/`); `mcp` =
the MCP server (`mcp/forge_mcp/config.py`); `addon` = the Blender add-on;
`rigbridge` = the UniRig sidecar. A variable read by more than one component is
read by all of them from the same name on purpose: two names for one folder is
a bug that only appears on the machine that set the variable.

**Never listed here:** anything set only by a test harness (those live in the
test files and the scanner skips them), and `FORGE_OT_*`, which is Blender's
operator class-name prefix and not an environment variable at all.

---

## The registry

| Variable | Component | Default | What it does |
|---|---|---|---|
| `FORGE_ANGULAR_TOLERANCE` | service | `0.2` | Angular deflection for tessellation, radians. |
| `FORGE_ASSISTANT_CLAUDE` | bridge | discovered | Full path to the Claude CLI, skipping discovery. The tests point it at `fake_claude.py`. |
| `FORGE_ASSISTANT_CWD` | bridge | the repo root | Working directory the CLI runs in. Must be the repo root or `.mcp.json` and `--resume` both stop resolving. |
| `FORGE_ASSISTANT_LIVE_CONTEXT` | bridge | `1` | The live Blender glance before a turn. `0`/`off`/`false`/`no` disables it â€” what a test harness sets so nothing probes the artist's live port. |
| `FORGE_ASSISTANT_MAX_UPLOAD_MB` | bridge | `20` | Cap on `POST /upload`, in megabytes. |
| `FORGE_ASSISTANT_MODEL` | bridge | unset | Fallback `--model`, used only when the request named none. Unset = the CLI's own configured default. |
| `FORGE_ASSISTANT_PORT` | bridge | `8901` | Listen port (loopback only). |
| `FORGE_ASSISTANT_PREVIEWS` | bridge | `<temp>/forge-webui-previews` | Where `POST /preview` renders its PNGs. Temp by design: regenerated on every click. |
| `FORGE_ASSISTANT_STALL_TIMEOUT` | bridge | `300` | Seconds the CLI may print *nothing at all* before the turn is stopped as hung. `0` switches the check off. A separate clock from `FORGE_ASSISTANT_TIMEOUT`: a process silent for five minutes is not a big request. |
| `FORGE_ASSISTANT_TEXT_INTERVAL` | bridge | `2.0` | Seconds between `text` activity markers; `0` records every chunk. |
| `FORGE_ASSISTANT_THUMBS` | bridge | `assistant/thumbs` | The library's thumbnail cache, one PNG per project. Not temp: a card has to draw before anything is running. |
| `FORGE_ASSISTANT_TIMEOUT` | bridge | `600` | Seconds one turn may take. Floored at 5. Reaching it is not a failure: the turn lands as `timeout` carrying everything the model said and did. |
| `FORGE_ASSISTANT_TOOLS` | bridge | `Read,Glob,Grep,mcp__forge__*` | The `--allowedTools` value. `""` is a legal setting and means a no-tools run. The assistant never holds `Write`; widening this is what would break that. |
| `FORGE_ASSISTANT_UPLOADS` | bridge | `assistant/uploads` | Where `POST /upload` writes. Beside the bridge so it is obvious to find and to delete. |
| `FORGE_BENCH_BRIDGE` | benchmark | `http://127.0.0.1:8901` | Bridge URL the benchmark runner submits its task turns to. Point it at a stub bridge to exercise the runner without paying for a model turn. |
| `FORGE_ASSISTANT_VERBOSE` | bridge | unset | Any non-empty value prints the HTTP access log. |
| `FORGE_BLENDER_CONNECT_TIMEOUT` | mcp | `2.0` | Seconds to wait for the Blender socket to accept. Short, so "is Blender up?" fails fast. |
| `FORGE_BLENDER_EXE` | bridge | discovered | Full path to `blender.exe`, for the two routes that *start* Blender rather than talking to it. Unset = `PATH`, then the installer's own folders. |
| `FORGE_BLENDER_HOST` | bridge, mcp | `127.0.0.1` | Host of the add-on's command socket. |
| `FORGE_BLENDER_PORT` | bridge, mcp | `9876` | Port of the add-on's command socket. The artist's live session owns 9876; every test suite uses a different port. |
| `FORGE_BLENDER_READ_TIMEOUT` | mcp | `180.0` | Seconds to wait for a command's reply. Generous: remesh blocks Blender's main thread. |
| `FORGE_CHECK_TIMEOUT` | service | `120` | Seconds for one `/check`. |
| `FORGE_COLD_START_EXTRA` | service | `60` | Extra seconds allowed for the first job, which pays the build123d import. |
| `FORGE_FLOWS_DIR` | mcp | `<repo>/flows` | The only folder `flow_save` writes to, and what `flow_list` reads. The add-on's `forge_flows_dir` preference must name the same folder. |
| `FORGE_FLOW_RUN_TIMEOUT` | bridge, mcp | `900` | Seconds a whole flow run may take â€” one flow can hold a 300 s `/segment` plus mesh loading. |
| `FORGE_FORCE_START` | bridge, service, meshgen | unset | `1` skips the "something is already answering on this port" guard and binds anyway. Every service allows address reuse, so without the guard a second start silently doubles up (B-6 of the 2026-09-16 dogfood run found two of each). |
| `FORGE_GENERATE_TIMEOUT` | bridge | `300` | Seconds one workbench rebuild may take. |
| `FORGE_MAX_RESPONSE_BYTES` | mcp | `268435456` | Refuse to buffer a response larger than this rather than eating all of RAM. |
| `FORGE_MESHGEN_BACKEND` | meshgen | `trellis2` | Which adapter is the default for a job that names none. |
| `FORGE_MESHGEN_BACKEND_MODULES` | meshgen | unset | Comma-separated importable modules contributing extra `Backend` classes, so a new adapter needs no edit to `backends/__init__.py`. |
| `FORGE_MESHGEN_COMFYUI_PORT` | meshgen | `8188` | Port the managed ComfyUI child listens on. |
| `FORGE_MESHGEN_COMFYUI_PYTHON` | meshgen | `C:/forge-models/comfyui/.venv/Scripts/python.exe` | The interpreter ComfyUI is launched with. Its own venv, so the heavy deps never leak into the other services. |
| `FORGE_MESHGEN_COMFYUI_ROOT` | meshgen | `C:/forge-models/comfyui` | The ComfyUI checkout. Out of the repo: it is part of the ~18.5 GB install. |
| `FORGE_MESHGEN_CONFIG` | meshgen | `meshgen/config.json` | Path to the config file read before the env overrides are applied. |
| `FORGE_MESHGEN_CONNECT_TIMEOUT` | mcp | `2.0` | Seconds to wait for meshgen's HTTP connection. |
| `FORGE_MESHGEN_HOST` | mcp | `127.0.0.1` | Host half of the meshgen address, when `FORGE_MESHGEN_URL` is unset. |
| `FORGE_MESHGEN_INPUT_DIR` | meshgen | `C:/forge-models/comfyui-input` | Where the service stages the image a job conditions on. |
| `FORGE_MESHGEN_JOB_TIMEOUT` | addon, mcp, meshgen | `900` (addon, mcp) / `1800` (meshgen) | How long a caller follows a job before handing back the id. The service's own ceiling is deliberately the longer one: the client giving up must not kill the work. |
| `FORGE_MESHGEN_MODELS_ROOT` | meshgen | `C:/forge-models/models` | Where the weights live. |
| `FORGE_MESHGEN_OUTPUT_DIR` | bridge, meshgen | `meshgen/config.json`'s `comfyui_output_dir` (`C:/forge-models/comfyui-output`) | Where the backends write their `.glb`, and therefore where the bridge's Models row looks. One variable for both halves so they cannot disagree. |
| `FORGE_MESHGEN_POLL_INTERVAL` | mcp | `3.0` | Seconds between `/job` polls. |
| `FORGE_MESHGEN_PORT` | mcp, meshgen | `8902` | The meshgen service's port. |
| `FORGE_MESHGEN_READ_TIMEOUT` | mcp | `30.0` | Seconds for one meshgen HTTP call â€” never the job itself, which is polled. |
| `FORGE_MESHGEN_STARTUP_TIMEOUT` | meshgen | `420` | Seconds to wait for the ComfyUI child to come up cold. |
| `FORGE_MESHGEN_URL` | bridge, mcp | `http://127.0.0.1:8902` | Full meshgen base URL; overrides host/port. |
| `FORGE_MESHOPT_DLL` | addon | `native/meshopt/meshoptimizer.dll` | Full path to the built meshoptimizer DLL for UV-preserving LOD simplification. Absent DLL = the LOD stage falls back to Blender Decimate and says so in every report. |
| `FORGE_MESH_SEW_TOLERANCE` | service | derived from the weld tolerance | Absolute mm tolerance for sewing an imported mesh into a solid, overriding the scale-relative default. |
| `FORGE_MESH_TRI_LIMIT` | service | `60000` | Triangles a mesh may bring to the `_mesh` endpoints; `0` removes the ceiling. |
| `FORGE_MESH_WELD_TOLERANCE` | service | `0.001` | Ceiling, in mm, on the scale-relative vertex weld for mesh input. |
| `FORGE_MODELS_DIRS` | bridge | unset | Extra folders the Models row indexes, `;`-separated, Windows-style â€” for meshes that came from somewhere else entirely. |
| `FORGE_MOLD_INPUT_DIR` | mcp | `%TEMP%/forge-mold-input` | Scratch folder where a Blender object is exported as STL on its way to the mesh-mold endpoints â€” triangles never cross the MCP wire. |
| `FORGE_MOLD_TIMEOUT` | service | `300` | Seconds for one `/mold`, `/export_mold`, `/mold_mesh` or `/export_mold_mesh`. |
| `FORGE_PREVIEWS_DIR` | mcp | `%TEMP%/forge-previews` | Where `render_preview` drops the PNGs the model then Reads. Scratch by design: never the repo, never a project. |
| `FORGE_PREVIEW_TIMEOUT` | mcp | `180.0` | Seconds to wait for one `render_preview`. |
| `FORGE_PRINTER_PATH` | mcp | `<repo>/templates/printer.json` | Default printer profile for the print-readiness tools. |
| `FORGE_PROJECTS_DIR` | addon, bridge, mcp | `<repo>/projects` | The one folder `partforge_new_part` writes into and the workbench, the panel and the library all read. Three components, one name â€” the single most important entry in this table. |
| `FORGE_RIGBRIDGE` | addon | `<repo>/rigbridge` | Where the joint-detector runner lives, for an install that moved it. A wrong path is reported, never guessed around. |
| `FORGE_RIGBRIDGE_CACHE` | rigbridge | `<rigbridge>/cache` | Detection results keyed by mesh hash + arguments + checkpoint, so re-tagging a mesh costs no GPU. |
| `FORGE_RIGBRIDGE_DISABLE` | rigbridge | unset | `1`/`true` switches joint detection off everywhere — every caller falls back to its non-detector path and says so. |
| `FORGE_SCRIPT_TIMEOUT` | service | `30` | Seconds one part-script run may take. |
| `FORGE_SEGMENT_TIMEOUT` | service | `300` | Seconds for one `/segment` or `/export_segments`. |
| `FORGE_SERVICE_CHECK_TIMEOUT` | mcp | `150.0` | Client budget for `/check`, deliberately above the service's own 120. |
| `FORGE_SERVICE_CONNECT_TIMEOUT` | mcp | `2.0` | Seconds to wait for the geometry service's HTTP connection. |
| `FORGE_SERVICE_HOST` | mcp | `127.0.0.1` | Host half of the geometry service address, when `FORGE_SERVICE_URL` is unset. |
| `FORGE_SERVICE_MOLD_TIMEOUT` | mcp | `330.0` | Client budget for the mold endpoints, deliberately above the service's own `FORGE_MOLD_TIMEOUT` of 300. |
| `FORGE_SERVICE_PACKAGE_ROOT` | mcp | `<repo>` | The folder *containing* `service/`, put on `sys.path` so maker mode can import the wiring maths in-process. Lazy and guarded. |
| `FORGE_SERVICE_PORT` | mcp | `8765` | The geometry service's port. |
| `FORGE_SERVICE_READ_TIMEOUT` | mcp | `180.0` | Seconds to wait for a build or export. |
| `FORGE_SERVICE_SEGMENT_TIMEOUT` | mcp | `330.0` | Client budget for `/segment` and `/export_segments`, above the service's own 300. |
| `FORGE_SERVICE_URL` | bridge, mcp | `http://127.0.0.1:8765` | Full geometry service base URL; overrides host/port. |
| `FORGE_SLICER` | service | detected | Full path to the slicer executable, overriding detection. |
| `FORGE_SLICE_TIMEOUT` | service | `600` | Seconds one slicer run may take. |
| `FORGE_START_SCRIPT` | bridge | `<repo>/start_forge.ps1` | What `POST /services/start` runs, hidden. A `.py` path runs under this interpreter instead, which is how the tests exercise the route without PowerShell. |
| `FORGE_TESSELLATION_TOLERANCE` | service | `0.05` | Linear deflection for tessellation, mm. |
| `FORGE_UNIRIG_PYTHON` | rigbridge | `<root>`'s own venv interpreter | The interpreter the detector subprocess runs under — UniRig's venv, never a Forge one; a missing interpreter is a named failure code, not a crash. |
| `FORGE_UNIRIG_ROOT` | rigbridge | `C:\forge-models\unirig` | The UniRig checkout. Out of the repo, like the rest of the model install. |
| `FORGE_UNIRIG_WEIGHTS` | rigbridge | `<root>`'s bundled weights | Explicit path to the UniRig weights; a path that does not exist is a hard error rather than a silent fallback. |

---

## Reading the defaults

A "default" here is what the component uses when the variable is **unset or
empty** â€” every reader treats an empty string as unset, so `set FORGE_X=` is not
a way to say "blank". Numeric readers fall back to the default on junk rather
than raising, because a typo in a variable should not stop a service from
starting; the value it settled on is visible on `/health` for the bridge, and
the component's own README table for the others.

Paths are `abspath`-ed by the reader, and on the bridge side also
`expandvars`/`expanduser`-ed where the variable is a list of folders
(`FORGE_MODELS_DIRS`).
