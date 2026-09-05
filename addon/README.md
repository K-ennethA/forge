# Forge — Blender add-on

The Blender half of the Forge pipeline. It does two things:

1. Runs a **command socket** on `127.0.0.1:9876` (newline-delimited JSON) that the Forge
   MCP server drives, so Claude can say "symmetrize", "voxel remesh at 1 mm", "export STL"
   and it happens in your open Blender session.
2. Renders the **PartForge panel**: sliders generated from a Build123d script's `PARAMS`
   block, a Regenerate button that reloads the solid in place, and an Export button.

Zero third-party dependencies — Python standard library plus `bpy`/`bmesh` only.

## Install

The package is `addon/forge/`. Pick either route:

**Zip install (normal use)**

1. Zip the `forge` folder itself so the archive contains `forge/__init__.py` (not the
   loose files at the archive root):
   ```powershell
   Compress-Archive -Path "C:\Users\<you>\...\forge\addon\forge" -DestinationPath "$env:TEMP\forge.zip" -Force
   ```
2. Blender → `Edit ▸ Preferences ▸ Add-ons ▸ ⌄ ▸ Install from Disk…` → pick `forge.zip`.
3. Tick **Forge** in the add-on list.

**Symlink (development — edits show up after a Blender restart or add-on re-enable)**

Run once in an **elevated** PowerShell:

```powershell
$src = "C:\Users\<you>\OneDrive\Desktop\git\forge\addon\forge"
$dst = "$env:APPDATA\Blender Foundation\Blender\<version>\scripts\addons\forge"
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
New-Item -ItemType SymbolicLink -Path $dst -Target $src
```

Then enable **Forge** in Preferences ▸ Add-ons. (On Blender 4.2+ the folder may be
`scripts/addons_core` for shipped add-ons; user add-ons still go in `scripts/addons`.)

## Start the server

- UI: `View3D ▸ N sidebar ▸ Forge ▸ Forge Server ▸ Start`.
- Preferences: `Edit ▸ Preferences ▸ Add-ons ▸ Forge` — set the port, the geometry
  service URL, timeouts, and **Start Server With Blender** for autostart.
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
| `symmetrize` | `direction` `+X…-Z`, `threshold?` | mirrors the named (surviving) half onto the other side |
| `mirror` | `axis`, `use_clip?`, `apply?`, `bisect?`, `flip?`, `merge_threshold?`, `mirror_object?` | adds (and optionally applies) a Mirror modifier |
| `remesh` | `mode` `voxel`\|`quad`, `voxel_size?`, `adaptivity?`, `target_faces?` | voxel remesh or Quadriflow retopology |
| `decimate` | `ratio`, `triangulate?` | collapse-decimates and applies |
| `shade` | `mode` `smooth`\|`flat`\|`auto`, `angle?` (degrees) | face shading; `auto` uses Blender 4.1+ Smooth-by-Angle |
| `apply_transforms` | `location?`, `rotation?`, `scale?` | applies transforms (all three when none are named) |
| `set_origin` | `type` `geometry`\|`bottom`\|`cursor`, `center?` | `bottom` = bounding-box bottom centre |
| `boolean` | `operand`, `operation`, `apply?`, `delete_operand?`, `solver?` | Boolean modifier, optionally applied |
| `merge_by_distance` | `distance` | welds close vertices, returns how many went away |
| `separate_loose` | — | splits loose parts, returns every resulting object name |
| `select_object` | `name`, `extend?` | selects and activates |
| `rename_object` | `name`, `new_name`, `rename_data?` | returns the name Blender actually used |
| `delete_object` | `name`, `purge_data?` | deletes the object (and its orphaned data) |
| `load_mesh` | `name`, `vertices` (mm), `faces`, `replace?`, `collection?`, `scale?` | builds a mesh; `replace` swaps mesh data in place, keeping the object, transforms, materials and custom properties |
| `export_stl` | `objects`, `path`, `scale?`, `ascii?`, `apply_modifiers?` | writes a binary STL |

Notes:

- **Units.** Blender works in metres, the geometry service in millimetres. `load_mesh`
  scales incoming vertex coordinates by `0.001` (objects keep scale 1.0). `export_stl`
  scales back up by `1000.0` by default so a 100 mm part lands in a slicer as 100 mm —
  pass `"scale": 1.0` if you want raw Blender units.
- **Paths** are absolute, normalised, and handle spaces, `~`, `%VARS%` and Blender's
  `//`-relative form. Parent folders are created for you.
- Handlers force OBJECT mode, restore the previous mode/selection/active object, and
  never assume a 3D viewport exists, so the same code paths work under
  `blender --background`.

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
```
