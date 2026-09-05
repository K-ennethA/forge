# Forge geometry service

The Build123d kernel behind PartForge. It takes a Python script with a `PARAMS`
block, runs its `build(p)`, and hands back a tessellated mesh in millimetres —
or writes an STL / STEP / 3MF file.

It also answers the question after that one: *can this be printed?* Bed fit,
wall thickness, overhangs and watertightness against a printer profile, and when
the answer is no, cutting the part into segments with dovetail, pin or magnet
joints and laying them out on one plate. See **Print readiness** below.

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
| `FORGE_CHECK_TIMEOUT` | `120` | seconds for one `/check` |
| `FORGE_SEGMENT_TIMEOUT` | `300` | seconds for one `/segment` or `/export_segments` |

Segmenting gets its own, larger budget because it is dozens of OCC booleans
rather than one `build()`; raising the interactive timeout to cover it would
make a runaway slider drag take five minutes to fail.

### Test it

```powershell
python -m pytest service/tests            # from the repo root
python -m pytest                          # from service/
```

`tests/test_params.py` and `tests/test_checks.py` are pure Python and run as
soon as pytest is installed. `tests/test_api.py` and
`tests/test_print_readiness_api.py` spawn the worker and skip themselves if
build123d is missing. `test_api.py` also exercises the containment story for
real: a deliberately hanging script must come back as a clean `400`, and the
next request must succeed on a fresh child. Those two tests are the slow ones —
they pay a worker restart on purpose.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `{"status": "ok", "build123d": "<version>", ...}` |
| `POST` | `/parse_params` | `{"script"}` | `{"params": <resolved schema>}` — no geometry built |
| `POST` | `/generate` | `{"script", "overrides"}` | `{"params", "mesh": {"vertices","faces"}, "stats"}` |
| `POST` | `/export` | `{"script", "overrides", "format", "path"}` | `{"path"}` |
| `POST` | `/check` | `{"script", "overrides", "printer"}` | `{"overall", "checks": [...], "stats", "printer"}` |
| `POST` | `/segment` | `{"script", "overrides", "printer", "joint", "mode"}` | `{"mode", "joint", "cuts", "segments": [...], "plate"}` |
| `POST` | `/export_segments` | the `/segment` body plus `{"directory", "basename", "format"}` | `{"files": [...], "plate_path"}` |

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
  `tessellate_ms`); `/check` adds `check_ms`, `/segment` adds `segment_ms` and
  `verify_ms`, `/export_segments` adds `export_ms`.

# Print readiness

Everything below is the Phase 2 half of the service: *can this be printed, and
if not, how do I cut it up so it can be?*

## Printer profiles

`/check`, `/segment` and `/export_segments` all take an optional `printer`
object — the contents of a `templates/printer.json`. Missing keys fall back to
the built-in Elegoo Centauri Carbon profile (256 mm cube bed, 0.4 mm nozzle,
0.8 mm minimum wall, 1.0 mm minimum feature, 50° overhang limit, `press_fit`
0.1 mm / `magnet_pocket_extra` 0.05 mm), and the merge is per-key, so
`{"bed": {"z": 120}}` changes only the bed height. The defaults live in
`printer.py` as a copy rather than a read of the template: the service is handed
profiles, it does not go looking for files. Keep the two in step.

Two knobs that are not in the profile because they are layout, not hardware:
`plate_margin_mm` (default 5) is the free space kept at each bed edge, and
`plate_spacing_mm` (default 5) the gap between two parts on a plate.

## `POST /check`

```jsonc
{"script": "...", "overrides": {}, "printer": { }, // all optional but `script`
 "plate_margin_mm": 5.0, "min_wall_probe_mm": null, "max_wall_samples": 4000}
```

returns

```jsonc
{"overall": "pass" | "warn" | "fail",
 "checks": [{"name": "bed_fit", "status": "...", "details": "...", "data": { }},
            {"name": "min_wall",   ...},
            {"name": "overhangs",  ...},
            {"name": "watertight", ...}],
 "printer": { }, "params": { }, "stats": { }, "timings": { }}
```

`overall` is the worst of the four. Nothing is written and no mesh comes back.

### `bed_fit`

The bounding box against the bed in each of the six axis-aligned orientations
(the label names the part axis pointing at the ceiling, so `"+Z"` is as
modelled) and both 90° placements on the bed. `pass` when something fits with
the margin, `warn` when it only fits without it, `fail` when nothing fits.

On `fail`, `data.suggested_segmentation` is the mode `/segment` would use:

```jsonc
{"kind": "radial" | "planar" | "none", "mode": {"radial": 4}, "feasible": true,
 "reason": "...", "estimated_segment_bbox_mm": [48.2, 212.1, 20.0],
 "metrics": {"ring_like": true, "outer_radius_mm": 150.0, "inner_radius_mm": 144.0,
             "material_ratio": 0.061, "central_axis_crossings": 0, ...}}
```

*Ring-like* means three things at once: a vertical line down the middle of the
bounding box hits no triangle, the footprint is no more than 1.6:1, and the
solid fills less than 70 % of its bounding box. Ring-like parts get radial cuts
(the smallest `N` whose wedge, spun to its tightest orientation, fits);
everything else gets planar Z cuts. `feasible: false` with a reason when neither
works — most often a footprint wider than the bed, which Z cuts cannot fix.

### `min_wall` — and what it cannot see

A ray is cast **inwards** from each sampled facet's centroid along the reversed
facet normal, and the distance to the first triangle it meets is the wall
thickness there. Rays stop at `min_wall_probe_mm` (four times the printer's
minimum wall by default, floor 3 mm): the question is only whether a wall is too
thin, so anything thicker than the probe is reported as "at least the probe" and
costs nothing more to find out. Thin hits are clustered onto a grid and reported
as `data.thin_regions`, thinnest first, with an approximate location.

`fail` below `min_wall_thickness`, `warn` between that and `min_feature_size`,
otherwise `pass`.

**This is a mesh approximation, not a B-Rep wall analysis.** It is wrong in
known directions, and a `fail` means "look here", not "this dimension is
0.61 mm":

- **It measures along the normal, not the shortest path.** A rib whose thinnest
  axis is not perpendicular to the surface being sampled — a wedge, a tapering
  fin — reads thicker than it prints.
- **Narrow gaps read as thin walls.** A ray leaving a concave pocket can cross
  empty space and hit the far side, so the number reported is a gap, not a wall.
  Conservative, but mislabelled.
- **It samples facets, not the surface.** One probe per facet: a thin spot in
  the middle of one big flat facet is invisible, and past `max_wall_samples`
  facets the facets are strided (`data.sample_stride`), which can skip a thin
  region entirely.
- **Curvature biases it.** Convex surfaces read thin, concave read thick.
- **Rays that hit nothing are `unmeasured`, not infinite.** When *every* probe
  runs its full length the check passes and says so; it does not pretend to know
  the thickness.

The authoritative dimensions are the ones in the script's `PARAMS`.

### `overhangs`

Per-facet angle **from vertical** — 0° is a wall parallel to the build
direction, 90° a flat ceiling, which is the convention
`printer.max_unsupported_overhang_deg` is written in. Facets whose highest point
is on the build plate are excluded: the plate supports them.

Every one of the six axis-aligned build directions is scored, and `data`
carries, per orientation, `unsupported_area_mm2`, `unsupported_facets`,
`unsupported_fraction`, `steepest_overhang_deg`, `fits_bed` and
`support_volume_estimate_mm3`. `best_orientation` is the lowest unsupported area
among the orientations that fit the bed.

The support volume estimate is **rough on purpose**: it is the column under each
unsupported facet down to the lowest point of the part, ignoring any material
already in the way. It over-estimates, sometimes badly. Use it to rank
orientations, never to predict filament.

Status is `pass` when the part as modelled has no unsupported facet, `warn`
otherwise — overhangs are printable with supports, so this check never fails.

### `watertight`

A restatement of the manifold analysis `/generate` already does (see *What
`watertight` means here*). No new geometry work.

## `POST /segment`

```jsonc
{"script": "...", "overrides": {}, "printer": { },
 "mode": "auto" | {"radial": 6} | {"planar": [30.0, 60.0]},
 "joint": {"type": "dovetail" | "pin" | "magnet" | "none", "tolerance": 0.1, ...},
 "include_mesh": true, "plate_margin_mm": 5.0, "plate_spacing_mm": 5.0}
```

returns

```jsonc
{"mode": {"kind": "radial", "count": 4, "start_angle_deg": 0.0},
 "suggestion": { },            // only when mode was "auto"
 "joint": { },                 // the request's joint, resolved
 "cuts": [{"name": "cut_1", "kind": "radial", "face_extent_mm": [6.0, 20.0],
           "segments": [3, 0], "joint": { /* sizes chosen for this face */ }}],
 "segments": [{"name": "segment_1", "kind": "segment" | "hardware",
               "stats": { }, "orient_deg": 44.86,
               "oriented_bbox_mm": [212.1, 48.2, 20.0],
               "mesh": {"vertices": [...], "faces": [...]}}],
 "plate": { }, "printer": { }, "params": { }, "stats": { }, "timings": { }}
```

- **Cut modes.** `{"radial": N}` makes `N` equal wedges about Z through the
  bounding-box centre in XY (add `start_angle_deg` to spin where the cuts land);
  each wedge is an exact circular sector, so a cut ring keeps its curvature.
  `{"planar": [z, ...]}` cuts at those Z heights, giving `len(z) + 1` slabs.
  `"auto"` uses `bed_fit`'s suggestion — including "no cuts needed", which comes
  back as one segment and an empty `cuts` list.
- **`kind`** is `"segment"` for a piece of the part and `"hardware"` for a pin
  that has to be printed to join two of them (named `cut_1_pin_1`, …).
- **`orient_deg`** is the rotation about the build axis that gives that segment
  its tightest footprint. The returned `mesh` is *not* rotated — `/segment`'s
  meshes stay assembly-accurate so a caller can show the part coming apart — but
  `oriented_bbox_mm`, the plate and every export apply it.
- **Watertightness is enforced, not reported.** Every segment is re-tessellated
  and re-analysed; a non-manifold segment is a `400` naming the segment and its
  edge counts, not a warning with a broken mesh attached.
- `include_mesh: false` drops the triangles and keeps the stats.

### Joints

Male features are built at nominal size and the matching negative is grown by
the tolerance, so the printed pieces sit `tolerance` apart across every mating
face. `tolerance` defaults to `printer.tolerances.press_fit` for `dovetail` and
`pin` and to `magnet_pocket_extra` for `magnet`; an explicit `joint.tolerance`
wins, and the response says which via `tolerance_source`.

Sizes are derived from the measured cut face — the service intersects the solid
with a 0.02 mm slab on the cut plane and takes the cross-section's extents — so
a joint scales with the part. Every field below can be pinned in the request.

| Type | Geometry | Defaults, from a cut face `across` × `along` mm |
|---|---|---|
| `dovetail` | A trapezoidal rail across the face, narrow at the face and wider at the tip: unioned onto one segment, cut (grown by the tolerance) out of the other. It runs the full length of the face, so the pieces assemble by sliding along it. | `width_mm` = `0.4 × across`, clamped to 2–12 mm and to 0.7 × `across`; `flare_mm` = `0.3 × width`, shrunk until the tail plus tolerance leaves material either side; `depth_mm` = `0.75 × width`, clamped to 1.5–8 mm |
| `pin` | A blind cylindrical socket in **both** faces (one cylinder spanning the cut cuts both at once) plus a separate printed pin per pair, emitted as its own segment. | `diameter_mm` = `0.45 × across`, clamped 2–6 mm; `socket_depth_mm` = `0.75 × diameter`, floor 2 mm; `pocket_diameter_mm` = `diameter + 2 × tolerance`; `pin_length_mm` = `2 × socket_depth − clearance_mm` (0.4 mm, so the pin seats before it bottoms out); ends chamfered 0.3 mm; `count` = 2 when the face is at least 4 pockets long, else 1 |
| `magnet` | A blind cylindrical pocket in both faces for a disc magnet. Nothing is printed to fill it. | `diameter_mm` 6.0, `thickness_mm` 3.0; `pocket_diameter_mm` = `diameter + 2 × tolerance` = 6.1; `socket_depth_mm` = `thickness + tolerance` = 3.05; same `count` rule |
| `none` | A plain cut. | — |

Two details worth knowing:

- On the dovetail's slanted flanks the in-plane offset is divided by the cosine
  of the flank angle, so the *perpendicular* clearance really is the tolerance
  asked for rather than a foreshortened version of it.
- Pins and magnets spread along the longer axis of the cut face; a dovetail runs
  along it. On a ring's radial cut that means the joint runs vertically and the
  segments slide together downwards.

A joint that cannot work on a given face is a `400` that says why and what to do
instead: a 6 mm magnet in a 2 mm wall, a dovetail whose middle lands on the hole
of an annular cut face, a socket deeper than half the material either side of
the cut.

**Known limitation:** a ring cut into `N` dovetailed arcs has every tail facing
the same way round the circle, so the last joint has to be sprung together.
Pins or magnets if that matters.

### The plate

`plate` is a shelf packing of the segments onto one bed — biggest first, each
part free to take a further quarter turn about Z:

```jsonc
{"bed_mm": [256, 256, 256], "margin_mm": 5.0, "spacing_mm": 5.0, "rows": 4,
 "used_mm": [212.6, 208.7], "fits": true,
 "items": [{"name": "segment_1", "index": 0, "pre_rotate_deg": 44.86,
            "rotate_deg": 0.0, "position_mm": [5.0, 5.0, 0.0],
            "size_mm": [212.1, 48.2, 20.0]}]}
```

`position_mm` is where that segment's bounding-box minimum corner goes, with Z
always 0 so everything sits on the plate. The total rotation applied is
`pre_rotate_deg + rotate_deg`. Packing is shelf rows, not a nesting solver: it
raises a `400` naming the offending segment rather than pretending, when a part
is taller than the bed, wider than the bed on its own, or when the rows run off
the back.

## `POST /export_segments`

The `/segment` body plus `directory` (absolute; created if missing),
`basename` (default `part`, sanitised — it cannot escape the directory) and
`format` (`stl` by default, also `step` or `3mf`). It writes:

- `<directory>/<basename>_<segment name>.<ext>` — one file per segment **and per
  pin**, each spun to its `orient_deg`, centred in XY and sitting on Z=0, so a
  slicer opening one file does not have to hunt for it.
- `<directory>/<basename>_plate.3mf` — one 3MF holding every segment at its
  packed plate position, one object per segment, ready to slice.

```jsonc
{"directory": "...", "files": [{"name", "kind", "format", "path", "orient_deg",
                                "stats"}],
 "plate": { /* as above, plus "path" */ }, "plate_path": "...",
 "mode": { }, "joint": { }, "cuts": [ ], "printer": { }, "timings": { }}
```

## `forge_lib` — appendage slots

Decorative pieces (ears, feet, fins, a tail) plug into a base through a *keyed
peg*: a cylinder with a flat rib down one side so the appendage cannot spin.
Because the peg and the socket come from one spec, changing the peg once changes
every socket.

`forge_lib` is available to a PartForge script the same way `build123d` is — it
is registered under its bare name, so `import forge_lib` works, and the name is
already bound in the script namespace for scripts that forget the import.

```python
from build123d import *
import forge_lib

PARAMS = {
    "peg_diameter": {"value": 6.0, "unit": "mm", "min": 3.0, "max": 20.0},
    "peg_length":   {"value": 8.0, "unit": "mm", "min": 3.0, "max": 40.0},
    "fit":          {"value": 0.2, "unit": "mm", "min": 0.0, "max": 1.0},
}

def build(p):
    spec = forge_lib.peg_spec(d=p["peg_diameter"], l=p["peg_length"])
    base = Pos(0, 0, 6) * Box(30, 30, 12)
    # socket_for() is a negative built pointing +Z from Z=0; flip it and put its
    # mouth on the face the appendage plugs into.
    socket = Pos(0, 0, 12) * Rot(180, 0, 0) * forge_lib.socket_for(spec, p["fit"])
    return base - socket
    # ...and the piece that plugs in is `my_ear + forge_lib.peg(spec)`.
```

| Call | Does |
|---|---|
| `peg_spec(d=6, l=8, key=True, key_width=None, key_height=None, chamfer=None)` | The single source of truth for one pair. `key_width` defaults to `0.35 × d`, `key_height` (the rib's radial protrusion) to `0.20 × d`. |
| `peg(spec)` — or `peg(6, 8)` | The keyed peg: axis +Z, base on Z=0, centred in XY, rib on +X, tip chamfered for insertion. |
| `socket_for(spec, tolerance=0.2, depth_extra=0.5, mouth_chamfer=0.2)` | The matching negative, same frame. The cavity is `tolerance` bigger on the cylinder radius and on both flanks and the tip of the key slot, and `depth_extra` deeper than the peg is long so it seats on its shoulder. |

`socket_for` also accepts a solid returned by `peg()` directly. The default
`tolerance` is `printer.json`'s `slide_fit` (0.2 mm) — the right starting point
for something meant to come apart; use `press_fit` (0.1 mm) for something glued
in once.

`samples/appendage_peg.py` is the working version of the example above: one
script that builds either the base or the ear, switched by `make_appendage`.

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
| `export.py` | STL / STEP / 3MF writers, the 3MF plate, and path handling |
| `printer.py` | Printer profile defaults, merging and validation |
| `checks.py` | The four print-readiness checks; pure mesh arithmetic |
| `joints.py` | Dovetail / pin / magnet geometry and the cut-face frame |
| `segmenting.py` | Cut modes, region solids, orientation, plate packing |
| `forge_lib.py` | The appendage peg/socket library scripts import |
| `samples/ring_band.py` | Reference PartForge script |
| `samples/appendage_peg.py` | Reference `forge_lib` script (base and ear) |
| `tests/` | pytest suite |

`checks.py`, `printer.py` and the packing half of `segmenting.py` deliberately
import no build123d, so they run — and are tested — in the HTTP process as well
as the worker. Everything that touches the kernel does so through a function-
local import, which is what keeps `import service.main` free of OCP.

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
