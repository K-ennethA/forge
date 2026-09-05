# Forge geometry service

The Build123d kernel behind PartForge. It takes a Python script with a `PARAMS`
block, runs its `build(p)`, and hands back a tessellated mesh in millimetres —
or writes an STL / STEP / 3MF file.

Blender's mesh tools are the wrong kernel for dimensioned, watertight,
print-ready solids, so this runs as a separate process and Blender just displays
what it produces.

- Listens on `http://127.0.0.1:8765` (loopback only, no auth — see *Security*).
- Wire contract: `docs/architecture.md`. Any change to the API happens there
  first.

## Setup

```powershell
cd C:\Users\kenne\OneDrive\Desktop\git\forge\service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Python 3.10 or newer. `build123d` pulls in OCP (the OpenCascade bindings),
which is a large wheel — the first install takes a few minutes.

Verified against **build123d 0.11.1** (cadquery-ocp 7.9.3, lib3mf 2.5.0) on
CPython 3.14. If `python` is not on PATH, use the interpreter's full path — on
this machine that is
`%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe`.

### Run it

```powershell
# either of these
forge-service
python -m service.main            # from the repo root
```

Options: `--port` (default 8765), `--host` (loopback names only),
`--log-level`, `--timeout` (wall-clock seconds per script run, default 30).

Environment overrides, useful for tuning without touching code:

| Variable | Default | Meaning |
|---|---|---|
| `FORGE_SCRIPT_TIMEOUT` | `30` | seconds one script run may take |
| `FORGE_COLD_START_EXTRA` | `60` | extra seconds allowed for the first job (build123d import) |
| `FORGE_TESSELLATION_TOLERANCE` | `0.05` | linear deflection, mm |
| `FORGE_ANGULAR_TOLERANCE` | `0.2` | angular deflection, radians |

### Test it

```powershell
python -m pytest service/tests            # from the repo root
python -m pytest                          # from service/
```

`tests/test_params.py` is pure Python and runs as soon as pytest is installed.
`tests/test_api.py` spawns the worker and skips itself if build123d is missing.
It also exercises the containment story for real: a deliberately hanging script
must come back as a clean `400`, and the next request must succeed on a fresh
child. Those two tests are the slow ones — they pay a worker restart on purpose.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `{"status": "ok", "build123d": "<version>", ...}` |
| `POST` | `/parse_params` | `{"script"}` | `{"params": <resolved schema>}` — no geometry built |
| `POST` | `/generate` | `{"script", "overrides"}` | `{"params", "mesh": {"vertices","faces"}, "stats"}` |
| `POST` | `/export` | `{"script", "overrides", "format", "path"}` | `{"path"}` |

`format` is `stl` (binary), `step` or `3mf`. `path` must be **absolute**;
parent directories are created.

Errors are `400` with `{"error", "traceback"}` when the script or its parameters
are at fault, `500` with the same shape when the service itself broke. There is
no `422`: request-validation failures are remapped to `400`.

`/health` always answers `200` so a caller can tell *service down* (connection
refused) from *service up, kernel missing* (`"status": "degraded"`).

### Extensions beyond the contract

All additive; nothing in `docs/architecture.md` changes shape.

- `/generate` and `/export` accept optional `tolerance` (mm) and
  `angular_tolerance` (radians) to trade mesh density against fidelity.
- `/parse_params` accepts an optional `overrides` object, so a caller can ask
  what the schema looks like with values already applied.
- `stats` carries extra diagnostic keys alongside the four contract ones:
  `bounding_box_min_mm`, `bounding_box_max_mm`, `bounding_box_source`,
  `solid_is_valid`, `mesh_is_closed`, `mesh_is_oriented`, `boundary_edges`,
  `nonmanifold_edges`, `degenerate_faces_dropped`.
- `/generate` includes a `timings` object (`resolve_ms`, `build_ms`,
  `tessellate_ms`).

## The PARAMS contract

```python
PARAMS = {
    "outer_diameter": {"value": 20.0, "unit": "mm", "min": 4.0, "max": 300.0,
                       "step": 0.5, "description": "Outer diameter"},
}

def build(p):
    ...  # returns a Build123d Part / Solid / Compound
```

- `value` is required. `unit` is required and is one of `mm`, `in`, `deg`,
  `count`, `ratio`, `bool`. `min` / `max` / `step` / `description` are optional.
- Parameter names must be valid Python identifiers — the Blender panel turns
  them into properties.
- Unknown keys in a spec are preserved verbatim, so scripts can carry hints the
  panel may learn to use later.

**Two unit vocabularies, deliberately kept apart.**

*Panel units* are what the schema and `overrides` speak: whatever the script
declared. A param with `"unit": "in"` and `"value": 0.5` shows 0.5 in the panel,
an override of `0.75` means 0.75 inches, and `min`/`max` are checked in inches.

*Build units* are what `build(p)` receives: millimetres (inches multiplied by
25.4), degrees for `deg`, `int` for `count`, `float` for `ratio`, `bool` for
`bool`. `build()` never sees inches.

`step` is a UI hint. The service does not snap values to it — snapping belongs
in the panel, where the user can see it happen.

Overrides are rejected, not silently fixed, when they name an unknown parameter,
fail type coercion, or fall outside `min`/`max`. The error names the parameter,
its value and the bound it broke. (`params.resolve(..., clamp=True)` clamps
instead; the HTTP API does not use it, but a panel that wants saturating
sliders can.)

`samples/ring_band.py` is the reference script.

## What `watertight` means here

Both of these must hold:

1. The B-Rep solid passes OpenCascade's own validity check
   (`Shape.is_valid()` → `BRepCheck_Analyzer`).
2. The mesh being handed to Blender is edge-manifold and closed: after welding,
   every edge is shared by exactly two triangles, with consistent winding.

The second is the one that matters for printing — a slicer sees triangles, not
a B-Rep — and it is measured on exactly the mesh that leaves the service.
`stats` reports both halves separately so a failure says which one broke.

## Layout

| File | Role |
|---|---|
| `main.py` | FastAPI app, request models, error contract, uvicorn runner |
| `params.py` | PARAMS execution, schema validation, coercion, unit conversion |
| `runner.py` | Tessellation, stats, and the warm worker process (parent side) |
| `worker.py` | The child process that imports build123d and runs the jobs |
| `export.py` | STL / STEP / 3MF writers and path handling |
| `samples/ring_band.py` | Reference PartForge script |
| `tests/` | pytest suite |

### Why a subprocess

A PartForge script is arbitrary Python. A thread stuck in a loop, or deep inside
OCC, cannot be killed from Python — so one runaway script would wedge the
service forever. A child process can always be killed, and that is the only
containment that actually holds.

The child is kept **warm** rather than spawned per request: importing build123d
costs several seconds, and paying that on every slider drag would miss the
"regeneration takes a second or two" target by an order of magnitude. One child
starts on first use, imports build123d once, then serves jobs over a
newline-delimited stdin/stdout protocol with the payloads passed through temp
files. On a timeout or a crash it is killed and the next request starts a fresh
one. Jobs are serialised behind a lock — OCC is not thread-safe, and
regeneration is a one-at-a-time operation anyway.

On Windows the child is spawned with `CREATE_NO_WINDOW`: nothing this service
does may flash a console on the user's desktop.

## Security

The service executes the scripts it is given, with full privileges. That is the
point — they are CAD scripts the user's own Claude session wrote — but it means:

- It binds loopback only. `--host` refuses anything that is not `127.0.0.1`,
  `::1` or `localhost`.
- There is no sandbox. `params.exec_script` uses a fresh namespace for
  determinism and clean tracebacks, not for confinement. The wall-clock timeout
  is the only containment, and it exists to stop runaway loops, not attackers.
- Do not point it at scripts from anywhere but this pipeline.
