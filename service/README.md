# Forge geometry service

The Build123d kernel behind PartForge. It takes a Python script with a `PARAMS`
block, runs its `build(p)`, and hands back a tessellated mesh in millimetres —
or writes an STL / STEP / 3MF file.

It also answers the question after that one: *can this be printed?* Bed fit,
wall thickness, overhangs and watertightness against a printer profile, and when
the answer is no, cutting the part into segments with dovetail, pin or magnet
joints and laying them out on one plate. See **Print readiness** below.

Past that it will hand a file to the slicer that is installed (**Slicing**) and
turn the same script into a two-piece **mold master** instead of the part
(**Mold mode**).

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
| `FORGE_MOLD_TIMEOUT` | `300` | seconds for one `/mold` or `/export_mold` |
| `FORGE_SLICE_TIMEOUT` | `600` | seconds one slicer run may take |
| `FORGE_SLICER` | — | full path to the slicer executable, overriding detection |

Segmenting gets its own, larger budget because it is dozens of OCC booleans
rather than one `build()`; raising the interactive timeout to cover it would
make a runaway slider drag take five minutes to fail. Mold mode is the same
shape of work and gets the same budget. Slicing is not our work at all — it is
another program's — and a fine layer height on a full plate is genuinely
minutes, hence the ten.

### Test it

```powershell
python -m pytest service/tests            # from the repo root
python -m pytest                          # from service/
```

`tests/test_params.py`, `tests/test_checks.py`, `tests/test_mold.py` and
`tests/test_slicer.py` are pure Python and run as soon as pytest is installed.
`tests/test_api.py`, `tests/test_print_readiness_api.py` and
`tests/test_mold_api.py` spawn the worker and skip themselves if build123d is
missing. `test_api.py` also exercises the containment story for real: a
deliberately hanging script must come back as a clean `400`, and the next
request must succeed on a fresh child. Those two tests are the slow ones — they
pay a worker restart on purpose.

Nothing in the suite needs a slicer installed. `tests/fake_slicer.py` is a stub
that answers the same command line, checks the same things and writes a file
where OrcaSlicer would write one, so `/slice` is tested — success, a missing
executable, a non-zero exit, and a hang that has to be killed — on any machine.

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
| `POST` | `/mold` | `{"script", "overrides", "printer", "parting_z_mm", "draft_deg", "shell_mm", ...}` | `{"halves": [...], "parting_z_mm", "draft", "spout", "vents", "registration_keys"}` |
| `POST` | `/export_mold` | the `/mold` body plus `{"directory", "basename", "format"}` | `{"files": [one per half]}` |
| `POST` | `/slice` | `{"input", "output", "profile", "printer", "slicer_path", "extra_args"}` | `{"output", "stdout_tail", "duration_ms"}` |

`format` is `stl` (binary), `step` or `3mf`. `path` must be **absolute**;
parent directories are created.

Errors are `400` with `{"error", "traceback"}` when the script or its parameters
are at fault, `500` with the same shape when the service itself broke. There is
no `422`: request-validation failures are remapped to `400`.

`/health` always answers `200` so a caller can tell *service down* (connection
refused) from *service up, kernel missing* (`"status": "degraded"`). It also
carries a `slicer` object — whether one was found, where, and if not, every path
that was probed — so a panel can grey out its *Slice* button before the user
presses it. A missing slicer never makes the service degraded: everything else
still works.

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
  `verify_ms`, `/export_segments` adds `export_ms`, `/mold` adds `mold_ms` and
  `verify_ms`, `/export_mold` adds `export_ms`.
- `/slice`'s two failure modes carry a `slicer` object alongside the contract's
  `error` and `traceback`: the probe report when nothing is installed, and the
  argv, exit code and output tails when something ran and refused.

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

# Mold mode

The plan's promise is one switch: *draft angles applied to vertical faces, a
two-piece mold box with a parting plane you can drag, registration keys, a pour
spout and vents.* `POST /mold` is that switch. What comes back are two **mold
masters** — you print them, then cast silicone or resin in them. Same script,
same `PARAMS`, same sliders as the direct-print version.

```jsonc
{"script": "...", "overrides": {}, "printer": { },
 "parting_z_mm": 4.0 | "auto",       // default "auto"
 "draft_deg": 2.0, "shell_mm": 4.0, "clearance_mm": 0.0,
 "spout": {"diameter_mm": 4.0, "position": [x, y]} | false,
 "vents": 2 | "auto", "registration_keys": 4,
 "include_mesh": true}
```

returns

```jsonc
{"halves": [{"name": "mold_top",    "stats": { }, "volume_mm3": 5786.7,
             "mesh": {"vertices": [...], "faces": [...]}},
            {"name": "mold_bottom", "stats": { }, "volume_mm3": 5774.4, "mesh": { }}],
 "parting_z_mm": 4.0, "parting_source": "auto" | "request",
 "parting_profile": [[z, area_mm2], ...],
 "draft": {"angle_deg": 2.0, "mold_top": "occ_draft", "mold_bottom": "occ_draft",
           "face_threshold_deg": 45.0},
 "box": {"shell_mm": 4.0, "size_mm": [28, 28, 16], "min_mm": [...], "max_mm": [...]},
 "cavity": {"volume_mm3": 938.7, "part_volume_mm3": 876.5, "clearance_mm": 0.0,
            "plain_half_volume_mm3": {"mold_top": 5802.7, "mold_bottom": 5802.7}},
 "spout": {"diameter_mm": 4.0, "position_mm": [9.5, 0.0], "top_z_mm": 12.5,
           "bottom_z_mm": 7.0, "length_mm": 5.5, "fully_inside_cavity": false},
 "vents": {"requested": 1, "count": 1, "diameter_mm": 1.0,
           "positions_mm": [[8.3, 4.5, 8.0]]},
 "registration_keys": {"count": 4, "radius_mm": 1.4, "tolerance_mm": 0.1,
                       "male_half": "mold_top", "female_half": "mold_bottom",
                       "positions_mm": [[-12, -12], [12, -12], [12, 12], [-12, 12]]},
 "bed": { }, "printer": { }, "params": { }, "options": { },
 "stats_part": { }, "timings": { }}
```

`plain_half_volume_mm3` is what each half weighed *before* the keys, spout and
vents touched it, so a caller can check that the features actually happened
rather than trusting that they did.

## The parting plane

`"auto"` takes the part's **widest horizontal cross-section**, measured on the
same welded mesh everything else here uses: every triangle straddling the plane
contributes one oriented segment, and the shoelace sum comes out as *outer loops
minus holes*, so a ring reports its annulus rather than its disc. Sixty-five
heights are sampled and the whole profile comes back as `parting_profile`, which
is exactly what a "drag the parting plane" slider wants behind it.

A part with a real bulge has one clear maximum. A prismatic part — a ring band,
a box — has a whole band of identical slices, and the tie breaks toward the
**middle** of that band: splitting a straight wall into two halves worth
printing beats shaving a sliver off one end.

An explicit `parting_z_mm` is honoured as given. Either way at least 0.5 mm of
part has to survive on each side, or it is a `400` naming the part's own Z span.

## Draft — what shipped, and what it costs

Two implementations, tried in order. `draft.mold_top` / `draft.mold_bottom` say
which one ran for each half, because they can differ.

| Method | How | Where it works |
|---|---|---|
| `occ_draft` | `BRepOffsetAPI_DraftAngle` on every face steeper than 45° from horizontal, with the far end of the half as the neutral plane. OCC tilts the faces and rebuilds the solid, so the result is smooth. | Anything whose walls are **planar, cylindrical or conical** — boxes, cylinders, rings, prisms, cones. OCC refuses everything else, face by face. |
| `taper_union` | The documented fallback. The half is fused with a series of copies of itself, each scaled up about a point behind the neutral plane, sweeping the silhouette outward toward the parting face. | Anything at all, including freeform surfaces. A sphere lands here. |
| `none` | `draft_deg: 0`, or nothing on the half could be tapered. | — |

Known limits, all of them real:

- **Draft adds material toward the parting line, it does not remove it from the
  far end.** A cavity that does not contain the part would cast a smaller part
  than the script describes, so the neutral plane sits at the far end and the
  cavity grows by up to `(half height) × tan(draft)` at the parting face. At the
  default 2° over a 10 mm half that is 0.35 mm. If the part's dimensions are
  what matter, use `draft_deg: 0` and taper in the script itself.
- **`taper_union`'s taper is a staircase.** Steps are one per millimetre of half
  height, clamped to 4–16, so sub-millimetre — but they are there, and they show
  in the printed master.
- **`taper_union` widens radially about the part's axis**, not normal to each
  face, so the taper is proportionally weaker near that axis than at the rim.
- **The two halves' cavity mouths do not match exactly.** Each is drafted from
  its own end, so the parting line can carry a step of the same order as the
  draft, which shows as flash on the casting.
- Containment is *verified*, not assumed: a drafted half that no longer contains
  the part, or that fails OCC's own validity check, is discarded and the next
  method is tried. `"none"` is the floor.

## The box, the keys, the spout and the vents

The box is the part's bounding box grown by `shell_mm` on all six sides, split
at the parting plane, with the cavity subtracted from each half.
`clearance_mm` (default 0) grows the cavity all round first, via OCC's 3D
offset; it fails loudly on a part OCC cannot offset rather than quietly doing
nothing.

**Registration keys** are spheres on the parting plane, spaced evenly around a
rectangle inset half the shell from the box edge — with the default four that is
the four corners. The boss is nominal on `mold_top`, the socket is grown by
`printer.tolerances.press_fit` in `mold_bottom`. A key that would break into the
cavity, or that needs more room than the shell has, is a `400` telling you to
raise `shell_mm` or set `registration_keys: 0`.

**The spout** is a cone from above the top of the box down into the cavity,
narrow at the bottom so it snips off. **Vents** are straight channels of
`max(min_feature_size, 1 mm)` from the cavity's other local high points to the
same top face; `"auto"` is one, plus one more per 40 mm of cavity width, capped
at four.

Both are placed from the mesh's high points, and both are then **walked across
the wall** until the channel's whole buried length is inside the cavity. Every
candidate a mesh offers is a vertex, which sits on an edge by construction: a
spout centred on a rim grazes it tangentially and that is exactly how a mold half
comes out non-manifold. When no position fits — a 2 mm wall cannot swallow a
4 mm spout — the original is used anyway and `spout.fully_inside_cavity` says
`false`. Diameters below `printer.min_feature_size` are a `400`; they would not
print.

## What is enforced

- **Both halves must be watertight.** They are re-tessellated and re-analysed
  exactly as segments are, and a non-manifold half is a `400` naming it and its
  edge counts — not a warning with a broken mesh attached.
- **Both halves must fit the bed**, box and all, in either 90° placement, with
  the plate margin. Otherwise it is a `400` that says to reduce `shell_mm`,
  shrink the part, or run `/segment` on the part first and mold the segments.

## `POST /export_mold`

The `/mold` body plus `directory` (absolute, created if missing), `basename`
(default `mold`, sanitised) and `format` (`stl`, `step` or `3mf`). It writes
`<directory>/<basename>_mold_top.<ext>` and `..._mold_bottom.<ext>`, each
centred in XY and sitting on Z=0, and returns the same report `/mold` gives plus
`files`.

A note on printing them: the halves come out modelled as they assemble, so
`mold_top` has its cavity facing down and its registration bosses hanging below
the parting plane. Slicing it as written asks for supports. Flip it in the
slicer — or in Blender — before you print it.

# Slicing

`POST /slice` hands a file this service exported to the slicer that is actually
installed, and brings back the G-code. It is a subprocess call, not a geometry
job, so it never goes near the warm worker.

```jsonc
{"input": "C:\\...\\band_mold_top.stl",      // absolute, .stl/.3mf/.step/.obj
 "output": "C:\\...\\band_top.gcode",         // absolute, .gcode or .3mf
 "profile": ["<machine>.json", "<process>.json"],   // or one path, or omitted
 "filaments": ["<filament>.json"],
 "printer": { }, "slicer_path": "...", "extra_args": [], "timeout_s": 600}
```

returns

```jsonc
{"output": "C:\\...\\band_top.gcode", "produced_name": "plate_1.gcode",
 "slicer": {"path": "C:\\Program Files\\OrcaSlicer\\orca-slicer.exe",
            "flavor": "orcaslicer", "source": "install"},
 "input": "...", "profiles": [...], "argv": [...], "returncode": 0,
 "stdout_tail": "...", "stderr_tail": "", "duration_ms": 549.1,
 "size_bytes": 539600, "printer": { }}
```

## Pointing it at a real install

Four sources, in order, first one that exists on disk wins:

1. `slicer_path` in the request,
2. the `FORGE_SLICER` environment variable,
3. the standard install locations — `%ProgramFiles%\OrcaSlicer\orca-slicer.exe`
   and its `(x86)` / `%LOCALAPPDATA%\Programs` variants, then the same three for
   Elegoo Slicer (`elegoo-slicer.exe`, which is an OrcaSlicer fork and takes the
   same flags), then the usual macOS and Linux paths,
4. `PATH`, for `orca-slicer` / `elegoo-slicer`.

`printer.slicer` (`"orcaslicer"` by default, from `templates/printer.json`)
decides which flavour is probed first when a machine has both.

**With nothing installed, `/slice` is a structured `400`, not a crash.** The
body keeps the contract's `error` and `traceback` and adds `slicer`, listing
every path that was probed and how to configure one. `GET /health` reports the
same detection result without running anything.

A `slicer_path` ending in `.py` is run under this interpreter, which is how the
test stub works — and is a genuine way to wrap a slicer in a script of your own.

## The command line

Every flag was verified by running **OrcaSlicer 2.3.2** on Windows, not read off
a wiki. They live in `slicer.CLI` as a dict rather than inline, so a release that
renames one is a one-line fix.

| Flag | What it actually does |
|---|---|
| `--debug 2` | OrcaSlicer is a GUI-subsystem binary and prints **nothing** without this. With it, warnings and errors go to stdout — that is what `stdout_tail` is worth reading for. (`--version` is not a valid option; the version is in the first `--debug` line.) |
| `--load-settings "<machine.json>;<process.json>"` | Semicolon-separated, one argument. A missing file is a clean non-zero exit with the path on stderr. A profile path containing `;` is rejected here rather than mangled there. |
| `--load-filaments "<filament.json>"` | Same shape, for `filaments`. |
| `--slice 0` | Slice all plates. Used when `output` ends in `.gcode`. |
| `--export-3mf <bare file name>` | Used when `output` ends in `.3mf`. The value is joined onto `--outputdir`, so it must be a plain name; an absolute path produces "Unable to open the file `<dir>/<abs path>`". |
| `--outputdir <dir>` | The only placement flag there is. |

Two things worth knowing:

- **`--slice` and `--export-3mf` must not be combined.** 2.3.2 writes the G-code
  and then dies with an access violation (`0xC0000005`). The output extension
  picks one or the other.
- **The slicer names its own G-code** — `plate_1.gcode`, not what you asked for.
  So `/slice` runs it into a scratch directory, takes what appeared there, and
  moves it to `output`; `produced_name` reports the name it had. That also means
  a version that *does* honour a name still works.

`extra_args` are appended verbatim, just before the model path.

OrcaSlicer's own profiles live under its install directory, e.g.
`C:\Program Files\OrcaSlicer\resources\profiles\Elegoo\machine\ECC\Elegoo Centauri Carbon 0.4 nozzle.json`
and `...\process\ECC\0.20mm Standard @Elegoo CC 0.4 nozzle.json`. Pass the
machine profile then the process profile. With no `profile` at all the slicer
uses whatever it last had configured, which is rarely what you meant.

## When it goes wrong

Everything is a `400` — all three are things the caller can fix — and each
carries a `slicer` object alongside `error` and `traceback`:

| Situation | `error` says | `slicer` carries |
|---|---|---|
| Nothing installed | how to configure one | `found: false`, `probed`, `configure` |
| Non-zero exit | the tail of the slicer's own stderr | `argv`, `returncode` (signed, so `-13` not `4294967283`), `stdout_tail`, `stderr_tail` |
| Overran `FORGE_SLICE_TIMEOUT` | that it was stopped, and to raise the limit | the same, plus `timed_out: true` |
| Exit 0, no file | to check the profile matches the model | the same |

The child is spawned with `CREATE_NO_WINDOW` and its stdin is `/dev/null`: a
slicer that decides to ask a question gets EOF instead of hanging on a prompt
nobody can see.

# Reference

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
| `mold.py` | Cross-sections, the parting plane, draft, the box and its features |
| `slicer.py` | Slicer detection, the CLI invocation, and running it hidden |
| `forge_lib.py` | The appendage peg/socket library scripts import |
| `samples/ring_band.py` | Reference PartForge script |
| `samples/appendage_peg.py` | Reference `forge_lib` script (base and ear) |
| `tests/` | pytest suite, plus `fake_slicer.py`, the stub `/slice` is tested against |

`checks.py`, `printer.py`, `slicer.py`, the packing half of `segmenting.py` and
the mesh half of `mold.py` deliberately import no build123d, so they run — and
are tested — in the HTTP process as well as the worker. Everything that touches
the kernel does so through a function-local import, which is what keeps
`import service.main` free of OCP.

`slicer.py` is the one module with no worker side at all: slicing is another
program's subprocess, there is no OCC state to serialise behind and nothing to
keep warm, so it runs in the HTTP process directly.

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
- `/slice` launches another program. The executable comes from `slicer_path`,
  `FORGE_SLICER` or a fixed list of install locations, and the arguments are
  built here as a list — never a shell string — so nothing in a path or a
  profile name can turn into a command. `extra_args` is the deliberate
  exception: it goes to the slicer verbatim, so treat it the way you would treat
  a command line you typed yourself.
