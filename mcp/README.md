# Forge MCP server

A stdio MCP server that gives Claude Code one tool surface over both Forge backends:

| Backend | Address | Used for |
|---|---|---|
| Blender add-on (`addon/forge/`) | TCP `127.0.0.1:9876` | scene inspection, common mesh ops, mesh loading, STL export |
| Geometry service (`service/`) | HTTP `127.0.0.1:8765` | PartForge parametric parts (Build123d) |

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
server on an ephemeral port), the 21-tool surface and its schemas, the backend-down error
messages, the stdio handshake against a real `python -m forge_mcp` subprocess, and the
`.mcp.json` registration. Nothing in the suite needs Blender or the geometry service, and
nothing binds or connects to 9876/8765.

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
| `FORGE_MAX_RESPONSE_BYTES` | `268435456` | refuse to buffer a runaway response |

## Tool catalog

### Health

| Tool | What it does |
|---|---|
| `forge_status` | Reachability of both backends in one call, with Blender/build123d versions. Never fails. |
| `blender_ping` | Cheapest check that Blender is up with the add-on server started. |

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

### PartForge

| Tool | What it does |
|---|---|
| `partforge_parse_params` | Reads a script's `PARAMS` block — names, values, units, ranges, descriptions. No geometry built. |
| `partforge_generate` | Builds the part **and** loads the mesh into Blender in one call (`load_mesh`, `replace=true`). Reports verts/faces/bbox/watertight. |
| `partforge_export` | Rebuilds and writes STL / STEP / 3MF straight from the solid. |

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
- **Meshes never reach the model.** `partforge_generate` moves the vertex/face arrays
  straight from the service into Blender and returns only stats.
- **Millimetres vs metres.** The service works in mm and `load_mesh` receives mm; the add-on
  scales by 0.001 on import. Blender-side tools (`voxel_size`, `merge_by_distance`) are in
  Blender units (metres).
- **Windows paths.** Every path argument is expanded (`~`, `%VARS%`), normalized and made
  absolute; missing export directories are created; export extensions are corrected to match
  the requested format.
