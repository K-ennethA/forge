# Forge — Blender add-on

The Blender half of the Forge pipeline. It does two things:

1. Runs a **command socket** on `127.0.0.1:9876` (newline-delimited JSON) that the Forge
   MCP server drives, so Claude can say "symmetrize", "voxel remesh at 1 mm", "export STL"
   and it happens in your open Blender session.
2. Renders the **PartForge panel**: sliders generated from a Build123d script's `PARAMS`
   block, a Regenerate button that reloads the solid in place, an Export button, and the
   print-readiness half — **Print Checks** (can this be printed?) and **Segments** (cut it
   up so it can be, and lay the pieces out on the plate).

Zero third-party dependencies — Python standard library plus `bpy`/`bmesh` only.

## Install

The package is `addon/forge/`. It ships **both** an extension manifest
(`blender_manifest.toml`, used by Blender 4.2+) and a legacy `bl_info` block (used by
Blender 4.0/4.1 and by the `scripts/addons` symlink route). The two coexist fine —
Blender picks whichever one matches how you installed it, and the add-on resolves its
own id either way, so preferences work under both.

Verified end-to-end on **Blender 5.0.1** (Windows) via all three routes below.

**Zip install (normal use, Blender 4.2+ / 5.0)**

1. Build the extension zip — this is the cleanest option, since it validates the
   manifest and leaves `__pycache__` out of the archive:
   ```powershell
   & "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --command extension build `
       --source-dir "C:\Users\<you>\OneDrive\Desktop\git\forge\addon\forge" `
       --output-dir "$env:TEMP"
   # -> $env:TEMP\forge-0.1.0.zip
   ```
   A plain `Compress-Archive -Path "...\addon\forge" -DestinationPath "$env:TEMP\forge.zip"`
   also installs correctly (Blender accepts a package inside one top-level folder); it
   just carries any stale `__pycache__` along.
2. Blender → `Edit ▸ Preferences ▸ Add-ons ▸ ⌄ ▸ Install from Disk…` → pick the zip.
3. Tick **Forge** in the add-on list.

Installed this way the add-on id is `bl_ext.user_default.forge`, which is what you will
see in `bpy.context.preferences.addons`. The operators are still `forge.start_server`
and friends.

**Symlink (development — edits show up after a Blender restart or add-on re-enable)**

Run once in an **elevated** PowerShell:

```powershell
$src = "C:\Users\<you>\OneDrive\Desktop\git\forge\addon\forge"
$dst = "$env:APPDATA\Blender Foundation\Blender\<version>\scripts\addons\forge"
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
New-Item -ItemType SymbolicLink -Path $dst -Target $src
```

Then enable **Forge** in Preferences ▸ Add-ons. Here the add-on id is plain `forge`.
Legacy `scripts/addons` add-ons still load on Blender 5.0. (On 4.2+ the folder may be
`scripts/addons_core` for shipped add-ons; user add-ons still go in `scripts/addons`.)

**Headless smoke test** (no window, nothing installed):

```powershell
blender --background --factory-startup --python-expr @'
import sys; sys.path.insert(0, r"C:\Users\<you>\OneDrive\Desktop\git\forge\addon")
import addon_utils; addon_utils.enable("forge")
import bpy; print(bpy.ops.forge.start_server())
'@
```

## Start the server

- UI: `View3D ▸ N sidebar ▸ Forge ▸ Forge Server ▸ Start`.
- Preferences: `Edit ▸ Preferences ▸ Add-ons ▸ Forge` — set the port, the geometry
  service URL, the printer profile, timeouts, and **Start Server With Blender** for
  autostart.
- Script/console: `bpy.ops.forge.start_server()`.

The panel shows whether it is listening, how many connections and commands it has
handled, and the last error (dismissable). Stopping the server releases the port; the
add-on also stops it on disable so re-enabling always works.

### Protocol

One JSON object per line, UTF-8, over TCP:

```
-> {"id": "1", "type": "symmetrize", "params": {"object": "Head", "direction": "+X"}}
<- {"id": "1", "status": "success", "result": {...}, "message": ""}
```

Errors always come back as `{"status": "error", "message": "..."}` — malformed JSON,
unknown commands and handler exceptions never kill the server, and the socket stays
open for the next request. Reads happen on a background thread; every command executes
on Blender's main thread through a `bpy.app.timers` pump.

Smoke test from any Python (no Blender needed on the client side):

```python
import json, socket
s = socket.create_connection(("127.0.0.1", 9876))
s.sendall(json.dumps({"type": "ping"}).encode() + b"\n")
print(s.makefile().readline())
```

## Commands

All object-targeting commands take `"object"` (a name); omit it to use the active object.

| type | params | does |
|---|---|---|
| `ping` | — | liveness + `blender_version` |
| `get_scene_info` | — | every object's name/type/location/dimensions/vertex count/modifiers, plus the active object |
| `execute_python` | `code`, `reset?` | runs code in a persistent namespace, returns captured stdout and the `repr` of a trailing expression |
| `symmetrize` | `direction` `+X…-Z`, `threshold?` | mirrors the named (surviving) half onto the other side; `result.method` says whether the bmesh or operator path ran |
| `mirror` | `axis`, `use_clip?`, `apply?`, `bisect?`, `flip?`, `merge_threshold?`, `mirror_object?` | adds (and optionally applies) a Mirror modifier |
| `remesh` | `mode` `voxel`\|`quad`, `voxel_size?`, `adaptivity?`, `target_faces?` | voxel remesh or Quadriflow retopology; `quad` refuses non-manifold input up front with a count of the offending edges |
| `decimate` | `ratio`, `triangulate?` | collapse-decimates and applies |
| `shade` | `mode` `smooth`\|`flat`\|`auto`, `angle?` (degrees) | face shading; `auto` uses Blender 4.1+ Smooth-by-Angle. `smooth`/`flat` remove any existing Smooth-by-Angle modifier, and repeated `auto` calls never stack them |
| `apply_transforms` | `location?`, `rotation?`, `scale?` | applies transforms (all three when none are named) |
| `set_origin` | `type` `geometry`\|`bottom`\|`cursor`, `center?` | `bottom` = bounding-box bottom centre |
| `boolean` | `operand`, `operation`, `apply?`, `delete_operand?`, `solver?` | Boolean modifier, optionally applied |
| `merge_by_distance` | `distance` | welds close vertices, returns how many went away |
| `separate_loose` | — | splits loose parts, returns every resulting object name |
| `select_object` | `name`, `extend?` | selects and activates |
| `rename_object` | `name`, `new_name`, `rename_data?` | returns the name Blender actually used |
| `delete_object` | `name`, `purge_data?` | deletes the object (and its orphaned data) |
| `load_mesh` | `name`, `vertices` (mm), `faces`, `replace?`, `collection?`, `scale?`, `plate?` | builds a mesh; `replace` swaps mesh data in place, keeping the object, transforms, materials and custom properties |
| `load_meshes` | `meshes` (a list of `load_mesh` param objects), `replace?`, `collection?`, `scale?`, `select?` | loads many meshes in one round trip; returns `{"objects": [...], "count", "names", "scale"}` |
| `export_stl` | `objects`, `path`, `scale?`, `ascii?`, `apply_modifiers?` | writes a binary STL |

### Additive protocol extensions (Phase 2)

Both are additions; nothing in `docs/architecture.md` changes shape.

- **`load_meshes`** — the plural of `load_mesh`. A cut part arrives as N segments plus any
  printed pins, and doing that as N round trips means N main-thread hops and N depsgraph
  refreshes for one logical action. Each entry of `meshes` takes exactly the params
  `load_mesh` does (and may carry its own `collection`); the top-level `replace`,
  `collection`, `scale` and `select` are the defaults for all of them. `select` applies to
  the last entry only. An empty list is an error, not a no-op.

  ```jsonc
  {"type": "load_meshes", "params": {
     "replace": true, "collection": "Segments",
     "meshes": [{"name": "segment_1", "vertices": [[x,y,z], ...], "faces": [[i,i,i], ...],
                 "plate": {"position_mm": [5.0, 5.0, 0.0],
                           "pre_rotate_deg": 44.86, "rotate_deg": 0.0}}]}}
  ```

- **`plate`** on `load_mesh` / each `load_meshes` entry — one entry of the geometry
  service's `/segment` `plate.items` list, **forwarded verbatim**. `position_mm` is where
  that segment's *rotated* bounding-box minimum corner goes and the total spin about Z is
  `pre_rotate_deg + rotate_deg`, so the add-on rotates the object, measures how far the
  rotated mesh's minimum corner sits from the origin, and offsets by the difference. The
  mesh data stays in assembly coordinates — only the object transform moves — which is what
  keeps a later `replace` cheap. The result gains `location` (scene metres) and
  `rotation_z_deg`. Omit `plate` and nothing is moved, exactly as before.

Notes:

- **Units.** Blender works in metres, the geometry service in millimetres. `load_mesh`
  scales incoming vertex coordinates by `0.001` (objects keep scale 1.0). `export_stl`
  scales back up by `1000.0` by default so a 100 mm part lands in a slicer as 100 mm —
  pass `"scale": 1.0` if you want raw Blender units.
- **Paths** are absolute, normalised, and handle spaces, `~`, `%VARS%` and Blender's
  `//`-relative form. Parent folders are created for you.
- **STL exporter.** Blender 4.2+/5.0 only ship `bpy.ops.wm.stl_export`; the legacy
  `io_mesh_stl` add-on (`bpy.ops.export_mesh.stl`) is gone from 5.0. `export_stl` prefers
  the new operator and keeps the old one as a fallback for 4.0/4.1, and reports which
  one ran in `result.exporter`.
- Handlers force OBJECT mode, restore the previous mode/selection/active object, and
  never assume a 3D viewport exists, so the same code paths work under
  `blender --background`.
- Operators that Blender *cancels* rather than raising on (modifier apply, voxel remesh,
  Quadriflow, STL export, symmetrize) have their return value checked, so a silent
  no-op comes back as `status: "error"` instead of a bogus success.

## PartForge panel

`View3D ▸ N sidebar ▸ Forge ▸ PartForge`.

1. Point **Script** at a Build123d `.py` file with a top-level `PARAMS` dict.
2. **Load Script** → the add-on POSTs the source to `/parse_params` and builds one
   field per parameter (float for `mm`/`in`/`deg`/`ratio`, integer for `count`,
   checkbox for `bool`). Values are clamped to each parameter's `min`/`max`; `step` and
   the description are shown behind the ⓘ toggle in the Parameters header.
3. **Regenerate** → POSTs to `/generate` and loads the returned mesh into the named
   object via the same `load_mesh` path, replacing the mesh data in place so your
   transforms, materials, modifiers and panel state survive.
4. **Export** → POSTs to `/export` with the chosen format (STL / STEP / 3MF). A blank
   export path defaults to `<script folder>/exports/<script name>.<format>`.

### Print Checks

`View3D ▸ N sidebar ▸ Forge ▸ PartForge ▸ Print Checks`.

**Run Checks** POSTs the script, the current parameter values and the printer profile to
`/check` and stores the answer on the scene. The panel then shows the overall verdict and
one row per check — `bed_fit`, `min_wall`, `overhangs`, `watertight` — each with an icon
(CHECKMARK pass / ERROR warn / CANCEL fail) and the service's own one-line detail. When
`bed_fit` fails the row also carries the suggested segmentation mode, and the Segments
panel below shows it as a hint while the mode is *Auto*.

The printer profile comes from the **Printer Profile** add-on preference, which defaults to
the repo's `templates/printer.json` in a source checkout. Leave it blank (or install from a
zip, where there is no repo above the add-on) and the panel sends no profile at all, so the
geometry service uses its built-in Elegoo Centauri Carbon defaults.

Two caveats the service documents and the panel inherits: `min_wall` is inward ray casting
on the mesh, so it is approximate — a fail means "look here", not an exact dimension — and
`overhangs` never fails, it warns, because supports exist.

### Segments

`View3D ▸ N sidebar ▸ Forge ▸ PartForge ▸ Segments`.

1. Pick a **Joint** (dovetail / pin / magnet / none) and optionally a **Tolerance**; 0
   means "use the printer profile" (`press_fit` for dovetail and pin, `magnet_pocket_extra`
   for magnets).
2. Pick a **Mode**: *Auto* (the service cuts the way `bed_fit` suggested), *Radial* with a
   wedge count, or *Planar* with a comma-separated list of Z heights in mm.
3. **Segment** POSTs to `/segment` with `include_mesh: true` and loads every returned piece
   as its own object, named after the segment, positioned and spun where the plate packing
   put it — so the viewport shows the print plate, not the assembled part. Re-running
   replaces the mesh data in place. Set **Segment Collection** to keep the pieces together.
4. **Export Segments** POSTs to `/export_segments`, writing one file per segment and per
   printed pin plus `<basename>_plate.3mf`. A blank folder defaults to
   `<script folder>/exports`, a blank basename to the script's name, and the format is the
   one chosen in the Export panel.

A joint that cannot work on a cut face (a 6 mm magnet in a 2 mm wall, a dovetail whose
middle lands on a hole) comes back as a `400` naming the problem; the message appears in the
status line and nothing is loaded.

**The PartForge panel needs the geometry service running on `127.0.0.1:8765`.** Without
it every button reports "Could not reach the geometry service…" in the panel's status
line. Use the globe button next to *Load Script* to check `/health`. Every HTTP call
runs on a worker thread with an explicit timeout and reports back through the status
line, so the UI never freezes waiting on the service. Regeneration is deliberately
manual — nothing is rebuilt until you press Regenerate.

## Layout

```
addon/forge/
  __init__.py            bl_info, add-on preferences, register/unregister
  prefs.py               add-on id + safe preference access
  server.py              TCP server, main-thread pump, start/stop operators
  tools/registry.py      command registry + error wrapping
  tools/common.py        every protocol command
  tools/partforge.py     PartForge state, HTTP client, operators
  ui/panels.py           sidebar panels
  blender_manifest.toml  extension metadata (Blender 4.2+ install path)
addon/tests/
  headless_phase2.py     headless checks for the Print Checks / Segments panels
```

## Headless tests

`addon/tests/headless_phase2.py` drives the Phase 2 panels through their real operators
inside a background Blender, against a running geometry service. Start the service on a
port that is not the one a live session uses, then:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python "C:\...\forge\addon\tests\headless_phase2.py" -- --service http://127.0.0.1:8769
```

It runs the checks on `service/samples/ring_band.py`, generates an oversized ring into a
temp folder (never the repo) and segments it radially with dovetails, exports the segments,
and exercises `load_mesh` / `load_meshes` over a real socket on port 9878. It prints one
line per assertion and ends with `RESULT: OK`.

Two things `--background` forces on the harness, and on anything like it:

- **There is no event loop, so `bpy.app.timers` never fires** — the add-on's main-thread
  pump included. The PartForge operators already run their HTTP work synchronously in
  background mode, but a socket command would sit on the queue forever, so the harness
  drains it itself: it sends from a thread and calls `server._server.drain()` in a loop on
  the main thread, which is exactly what the timer would have done.
- **No window, no viewport, no 3D area.** Every handler under test has to work without one,
  which is half the reason for testing it here.
