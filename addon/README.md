# Forge — Blender add-on

The Blender half of the Forge pipeline. It does four things:

1. Runs a **command socket** on `127.0.0.1:9876` (newline-delimited JSON) that the Forge
   MCP server drives, so Claude can say "symmetrize", "voxel remesh at 1 mm", "export STL"
   and it happens in your open Blender session.
2. Renders the **PartForge panel**: sliders generated from a Build123d script's `PARAMS`
   block, a Regenerate button that reloads the solid in place, an Export button, and the
   print-readiness half — **Print Checks** (can this be printed?) and **Segments** (cut it
   up so it can be, and lay the pieces out on the plate).
3. Renders the **RigForge panel**: semantic tags on a sculpt, the `character.json`
   manifest, one-click retopology and auto-UV (Phase 3), then the rig itself — a Rigify
   metarig fitted to those tags, generated, weighted and cleaned up, and exported to a
   Godot-ready glTF (Phase 4) — and finally clothes and motion: a garment grown from the
   tags with its cloth sim baked to a shape key, the Godot action library, structured
   keyframing and mocap retargeting (Phase 5). That is the whole run: sculpt in, dressed
   and animated playable character out.
4. Renders the **Assistant panel** near the top of the tab (Phase 6): a chat box where you
   describe what you want in ordinary words. It talks to the assistant bridge
   (`assistant/bridge.py`, port 8901), which runs the Claude Code CLI headless with the
   Forge MCP tools. Every other panel assumes you know which button you want; this one
   does not.
5. Renders the **Forge Status row** above everything (the UI batch): four dots saying
   whether the shape service, the assistant, the Blender link and your sign-in are
   actually there, and one button that starts whatever is not. "Why did nothing happen"
   is answered at the top of the tab instead of three clicks deep in an error message.

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
  service URL, the printer profile, the assistant bridge URL, the picture-to-3D URL
  (`meshgen_url`, default `http://127.0.0.1:8902`), the flows folder (`forge_flows_dir`,
  default `<repo>/flows`), timeouts, and **Start Server With Blender** for autostart.
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

### Undo checkpoints

Every command that can change the scene pushes a **named undo step before it runs**
(`bpy.ops.ed.undo_push(message="Forge: <command>")`, in `tools/registry.py`'s `dispatch`).
So Ctrl+Z in the viewport reverses what the assistant just did, `Edit ▸ Undo History` shows
it in words, and the Assistant box's **Revert last AI action** button is a real button
rather than an apology.

* Read-only commands push nothing: `ping`, `get_scene_info`, `flow_list`,
  `rigforge_list_tags`, `rigforge_status`, `export_stl` (it writes a file, which undo could
  never take back anyway), `render_preview` (looking at the scene is not changing it — it
  borrows the render settings and a camera and puts every one of them back, so a checkpoint
  for it would take back whatever the artist actually wanted undone) and the geometric gate,
  `verify_design` and `turntable`, for exactly those two reasons — one measures, the other
  borrows and returns. Burying the checkpoint
  the artist wants under a pile of `ping`s would defeat the point.
* `execute_python` **does** push one, deliberately: arbitrary code is exactly the case
  worth being able to take back, even when it happened to do nothing.
* The push is *before* the handler, so a command that fails half-way through is the one
  you can most easily undo.
* Verified working in `--background` (Blender 5.0 pushes and pops with no window), but
  guarded regardless: the first refusal flips checkpoints off for the session, prints one
  line, and every command keeps working. A trust feature must never break the flows it
  exists to protect.
* Flows push one step per Blender step, because a flow *is* a sequence of those commands.

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
| `load_meshes` | `meshes` (a list of `load_mesh` param objects, or `/segment` segments), `plate?`, `replace?`, `collection?`, `scale?`, `select?` | loads many meshes in one round trip; returns `{"objects": [...], "count", "names", "scale"}` |
| `export_stl` | `objects`, `path`, `scale?`, `ascii?`, `apply_modifiers?` | writes a binary STL |
| `load_reference` | `path`, `view` `front`\|`side`\|`top`, `size_mm?`, `name?`, `offset_mm?`, `collection?` | puts a sketch/photo in the viewport as an image EMPTY facing that orthographic view. Returns `{"object", "width_mm", "height_mm", "view", "size_mm", "path", "image", "pixels", "replaced", "location", "rotation_deg", "opacity"}` |
| `render_preview` | `path` (`.png`), `objects?` (default every visible mesh), `resolution?` (128–2048, default 768), `view?` `iso`\|`front`\|`side`\|`top`, `shading?` `solid`\|`material` | renders the scene to a PNG the assistant can Read — a temporary orthographic camera fitted to the target bounds, Workbench clay by default, **every borrowed setting put back**. Returns `{"path", "objects", "resolution", "view", "shading", "engine", "size_bytes", "framed_all_visible", "bounds_mm": {"min", "max", "size"}, "ortho_scale_mm", "notes"}` |
| `partforge_open` | `script_path`, `keep_values?`, `object?`, `params?` | points the PartForge panel at a script and rebuilds its sliders — the panel's own Load Script path, driven from outside. Returns `{"script", "param_count", "params", "object", "schema_source"}` |
| `flow_list` | — | every saved flow in the flows folder: `{"dir", "count", "flows": [{"name", "description", "params", "steps", "step_labels", "path"}]}`. A file that will not parse is listed with an `error` instead of being hidden |
| `check_model` | `object?`, `printer?` | print-checks a mesh that is **already in the scene** (a downloaded STL, your own sculpt): the evaluated mesh goes to the service's `/check_mesh` in millimetres and the rows land in the Print Checks panel. Returns `{"object", "overall", "checks", "printer"?, "stats"?, "mesh": {"vertex_count", "face_count", "scale"}, "printer_source", "panel"}` |
| `segment_model` | `object?`, `printer?`, `joint?`, `mode?`, `collection?` | cuts an in-scene mesh via `/segment_mesh` and loads the pieces laid out on the plate. Returns `{"object", "objects", "count", "segments", "mode", "joint", "plate", "mesh", "printer_source"}` — the segment meshes are **not** echoed back, the objects are in the viewport |
| `import_generated` | `path` (`.glb`/`.gltf`), `name?`, `repair?` (**default true**), `voxel_size?` (scene metres), `collection?` | imports what meshgen wrote and **voxel-repairs it on the way in**. Returns `{"object", "vertex_count", "face_count", "edge_count", "repaired", "path", "importer", "imported_objects", "dimensions_mm", "before": {...}, "voxel_size"?, "voxel_size_mm"?, "repair_method"?}` |
| `profile_from_curve` | `curve_object`, `points?` (5–10, default 7), `close_bottom?` (default true) | **read-only.** Samples a curve the artist DREW into the `(radius, z)` control points `forge_lib.soft_body` takes. Returns `{"object", "points_mm", "height_mm", "max_radius_mm", "min_radius_mm", "base_radius_mm", "plane", "plane_normal", "point_count", "sample_count", "spline_type", "closed_curve", "close_bottom", "z_offset_mm", "flatness_mm", "helper", "notes"}` |
| `outline_from_curve` | `curve_object`, `points?` (6–16, default 12), `recenter?` (default true) | **read-only.** Samples a **closed** drawn curve into the `[x, y]` outline `forge_lib.silhouette_part` extrudes. Returns `{"object", "points_mm", "width_mm", "height_mm", "plane", "plane_normal", "point_count", "sample_count", "spline_type", "closed_curve", "cyclic_flag", "recentered", "offset_mm", "self_intersections", "flatness_mm", "helper", "notes"}` |
| `merge_for_print` | `objects?` (names), `collection?`, `voxel_size_mm?` (omit or `0` = nozzle/2), `name?` (default `<project>-merged`), `keep_originals?` (default true) | joins the chosen meshes and voxel-remeshes them into ONE watertight shell. Returns `{"object", "vertex_count", "face_count", "edge_count", "voxel_size_mm", "voxel_size_requested_mm", "voxel_source", "nozzle_mm", "printer_source", "predicted_face_count", "surface_area_mm2", "watertight_input_count", "watertight", "loose_vertices", "sources", "source_count", "resolved_by", "collection", "kept_originals", "hidden", "deleted", "dimensions_mm", "remesh_method", "next", "notes"}` |
| `save_project_blend` | `project?` (default: the panel's project), `create?` (default true) | **read-only for undo.** Saves the scene to `projects/<name>/<name>.blend` as a **copy** — the artist's own file is never retargeted. Returns `{"project", "path", "folder", "size", "mtime", "exists", "replaced", "created_folder", "object_count", "objects", "session_file", "retargeted": false, "changed", "where"}` |
| `open_project_blend` | `name`, `confirm?` (default false) | **read-only for undo.** Opens that project's `.blend`. With unsaved work and no `confirm` it returns `{"needs_confirmation": true, "would_lose", "objects", "hint", ...}` and touches nothing; confirmed it returns `{"opened": true, "discarded", "session_file", "object_count", "objects", "server_running", "server_port", "pump_survived", ...}`. No file yet is an error naming the button that makes one |
| `flow_run` | `name` \| `flow` (an inline flow object), `params?` | replays a saved sequence — Blender steps through the command registry, service steps over HTTP. Linear and fail-fast. Returns `{"flow", "description", "params", "count", "ok", "duration_ms", "steps": [{"index", "kind", "op", "label", "ok", "brief"}]}` |

### Workspace commands (Phase 8 — the copilot drives the viewport)

Seven commands that change what the artist is looking at, what mode they are in and
what brush is in their hand. Every one returns **`changed`** (one plain sentence
naming what is different) and **`where`** (the place in Blender's own UI they would
have clicked). Those two fields are the whole teaching contract — the assistant is
told to reply with both, in one line.

All seven are in `READ_ONLY_COMMANDS`, so **none of them pushes an undo checkpoint**.
Blender does not put viewport state in the undo stack at all, and a mode switch or a
brush size in the undo history is noise on top of the artist's actual work.
Transparency replaces undo: the `changed` line is what makes the change reversible
by hand.

**Which viewport:** every VIEW_3D area in every window. An artist with a quad split
or a second monitor asked for the grid *on*, not for the grid on whichever area
Blender happened to hand us. Quad-view sub-regions are turned too. `capture_viewport`
is the one exception — a picture is one picture, so it uses the **largest** VIEW_3D.
The count is reported as `viewports` in every result.

**Headless:** `blender --background` still builds one off-screen screen with a
VIEW_3D in it, which is exactly the trap — the commands would "succeed" against a
viewport nobody is looking at. So the guard is on `bpy.app.background`, not on "is
there an area", and the five view commands plus `capture_viewport` refuse with one
sentence (`workspace.NO_VIEWPORT`). `set_mode` and `sculpt_brush` do **not** refuse:
mode lives on the object and brush settings live in the scene's tool settings, so
both are real work with no window involved.

| type | params | does |
|---|---|---|
| `set_view` | `view` `front`\|`back`\|`left`\|`right`\|`side`\|`top`\|`bottom`\|`iso`\|`camera`, `ortho?` | turns every 3D viewport to that angle. `ortho` defaults to **flat for the axis views** and perspective for `iso`, which is Blender's own habit; `front`/`side`/`top` are the same three projections `render_preview` and `load_reference` use, so a front viewport and a front render line up. `camera` refuses with a sentence when the scene has no camera. Returns `{"view", "ortho", "viewports", "regions", "changed", "where"}` |
| `frame_object` | `object?`, `all?`, `margin?` (1.0–4.0, default 1.25) | centres and zooms every viewport onto that object (or every visible mesh). **Nothing is selected or deselected** — the artist's selection is theirs. Returns `{"objects", "framed_all_visible", "center_mm", "size_mm", "radius_mm", "view_distance", "viewports", "changed", "where"}` |
| `local_view` | `enable?` (omit to toggle), `object?` | isolates the selection so everything else stops getting in the way. Naming an `object` selects it and makes it active first, because Local View isolates the *selection* and there is no other way to say which one was meant. Returns `{"enabled", "object", "viewports", "viewports_changed", "changed", "where"}` |
| `set_shading` | `mode` `solid`\|`wireframe`\|`material`\|`rendered` | the four shading balls, top right. Returns `{"mode", "viewports", "changed", "where"}` |
| `set_overlays` | `grid?`, `axes?`, `wireframe?`, `stats?`, `overlays?`, `origins?`, `cursor?`, `text?`, `face_orientation?`, `xray?` | **the motivating case.** `{"grid": true, "axes": ["x","y","z"]}` is the whole of "I want to enable grid view for x,y,z axis". `grid` sets both the floor grid and the orthographic grid (two switches, one idea). `axes` takes a list, `"all"`, `true`, or `[]`/`false`/`"none"` — and **the list is authoritative**: an axis not in it is turned off. `xray` lives on the shading, not the overlay, which is where Blender keeps it. Anything not named is left exactly as the artist had it. Returns `{"applied", "requested", "viewports", "changed", "where"}` |
| `set_mode` | `mode` `object`\|`edit`\|`sculpt`\|`vertex_paint`\|`weight_paint`\|`texture_paint`\|`pose`, `object?` | validates that the object type supports the mode **before** asking Blender ("Sculpt Mode only works on mesh objects"), leaves any other object's mode first, then selects and activates the target — Blender's mode belongs to the active object, and switching without that is the commonest silent no-op. Asking for a mode you are already in is a success, not an error. Spaces are accepted for underscores (`"vertex paint"`). Returns `{"mode", "blender_mode", "object", "object_type", "previous", "changed", "where"}` |
| `sculpt_brush` | `brush?`, `size?` (1–5000 px), `strength?` (0–10), `symmetry_x/y/z?`, `dyntopo?`, `object?`, `enter_mode?` (default true) | picks the brush and sets it up. Enters Sculpt Mode first by default, because that is what makes the brush list exist. Every numeric parameter is validated **before** any mode or brush is touched. Returns `{"object", "brush", "brush_type", "size", "strength", "symmetry", "dyntopo", "mode", "available", "changed", "where"}` |

**Brush names in Blender 4.3+.** Brushes became *assets*: `bpy.data.brushes` holds
only the ones this file has already used (one, in a fresh scene) and `Paint.brush`
is read-only. Validating against `bpy.data.brushes` alone would therefore reject
`"Clay Strips"` in every scene where the artist had not already clicked it. So the
catalog is the union of this file's brushes and **Blender's own essentials library**
(62 sculpt brushes, read once from `datafiles/assets/brushes/essentials_brushes-mesh_sculpt.blend`
and cached), and switching goes through `bpy.ops.brush.asset_activate` with a direct
`Paint.brush` assignment tried first for Blender 4.2 and earlier. Matching is
case-insensitive and treats `_`/`-`/space alike; a miss comes back with a
`difflib` closest match — the `rigforge_keyframe` precedent — plus the list of what
is actually there: *"There is no sculpt brush called 'Smoothify'. Did you mean
'Smooth'? Brushes available: Airbrush, Blob, Clay, Clay Strips, ..."*

### Buddy commands (Phase 8 — a teacher's eyes)

| type | params | does |
|---|---|---|
| `capture_viewport` | `path` (`.png`), `resolution?` (128–4096, default 1024 — the **longer** side; the shorter one follows the viewport's own aspect) | photographs the **largest** 3D viewport with an OpenGL render under a context override: the artist's angle, their shading, their overlays, the mask they have painted. Borrows the scene's render settings and puts every one of them back. Read-only; refuses headless. Returns `{"path", "resolution", "size_bytes", "area", "shading", "perspective", "mode", "viewports", "note"}` |
| `mesh_diagnose` | `object?`, `examples?` (1–25, default 5), `apply_modifiers?` (default true), `density_ratio?` (1.5–100, default 4) | numeric defects, each with a place in millimetres. Read-only. Returns `{"object", "vertex_count", "face_count", "edge_count", "evaluated", "self_intersections", "topology", "zero_area_faces", "ngons", "loose", "density", "scale", "notes", "duration_ms", "verdict", "clean"}` |
| `verify_design` | `object?`, `for?` `game`\|`print`\|`any` (default `any`), `reference_image?` (a picture on disk), `poly_budget?` (faces; default 15 000 for `game`, ungated otherwise), `symmetry_axis?` `X`\|`Y`\|`Z` (default `X`), `examples?` (1–25) | **the geometric gate.** One scored report over six axes, each with a `status` and a credibility `tier`. Read-only. Returns `{"object", "for", "gated_axes", "axes", "attention", "passed", "gate", "face_count", "vertex_count", "tiers", "notes", "duration_ms", "verdict"}` |
| `turntable` | `object?` / `objects?`, `views?` (4–64, default **24**), `resolution?` (64–512 px per tile, default **256**), `path` (`.png`) or `dir`, `elevation?` (−80…80°, default 15), `keep_frames?` (default false) | **the standardised judging rig.** N views around Z stitched into ONE contact sheet, every tile framed identically. Read-only; borrows the render settings and a camera and puts every one of them back. Returns `{"path", "objects", "views", "resolution", "columns", "rows", "sheet_size", "elevation_deg", "angles_deg", "reading_order", "frames", "kept_frames", "framed_all_visible", "bounds_mm", "fixed_framing", "size_bytes", "duration_ms", "notes"}` |

`mesh_diagnose` in detail:

| block | what it holds |
|---|---|
| `self_intersections` | `{count, faces, examples: [{location_mm, faces}], scanned, note?}` — the artist's **clipping**. A `BVHTree.overlap` against itself, with every pair that shares a vertex filtered out (adjacent faces touch by definition; that is not clipping). Capped at **200 000 faces**, and above that it says so in `notes` rather than silently not running |
| `topology` | `{non_manifold_edges, boundary_edges, wire_edges, multi_face_edges, non_manifold_vertices, loose_vertices, watertight, edge_examples, vertex_examples}` — each example carries `location_mm` and a plain-words `kind` (`"open hole"`, `"loose wire"`, `"3 faces meet here"`) |
| `zero_area_faces` | `{count, examples}` — degenerate geometry that survives remeshes and breaks normals |
| `ngons` | `{count, max_sides, examples}` |
| `loose` | `{vertices, wire_edges, shells}` — `shells` is a union-find over the edges, so "the mesh is in 5 separate pieces" is answerable |
| `density` | `{faces: {count, mean_mm2, median_mm2, min_mm2, max_mm2, p05_mm2, p95_mm2}, ratio, dense: [...], starved: [...]}` — each region is `{faces, mean_area_mm2, location_mm, times_median}`. **"We need to remesh here" is a `starved` entry** |
| `scale` | `{object_scale, non_uniform, unapplied, mirrored, dimensions_mm, problems: [sentences]}` |
| `verdict` | the three or four sentences a teacher would actually say, worst first — clipping, then unsealed edges, then starved, then crammed |
| `clean` | true only when there is nothing at all to say |

**How a region is decided.** A face has to be `density_ratio` off the median **and**
in the most extreme 2% of the mesh, and a cell has to hold at least 6 offending faces
before it is a place. Without the second and third tests a plain UV sphere reports its
own poles on every single check-in — the pole triangles really are seven times smaller
than the equator quads — and an artist who reads that twice stops reading. The
bounding box is bucketed 8 cells per axis, which is fine enough to point at an ear and
coarse enough that one cell is one sentence.

**Speed.** Measured on this machine: **0.65 s on 198 916 faces** with the
self-intersection scan, 0.39 s on 202 500 without it (above the cap). Bulk statistics
come off the Mesh with `foreach_get` into numpy arrays; topology comes off a bmesh;
the BVH overlap is the expensive one and is the thing that is capped.

### Mechanism demo commands (Phase 17 — showing how it works)

Three commands in `tools/mechanism.py`, and between them the robotics-site trick: a
part that presses, a light that comes on, and a short film of the two happening
together. The motion is the numbers `plunger_plan` already computed, played back —
**an illustration of the intended motion, not a simulation.** Nothing here computes a
force, a spring rate or a collision, and the system prompt requires the assistant to
say so in words every time it shows one.

| type | params | does |
|---|---|---|
| `animate_object` | `object?`, `keys` (`[{"frame", "location"? (metres), "location_mm"? (millimetres), "rotation_euler_deg"?, "scale"?}]`), `interpolation?` (default `BEZIER`), `clear?` (default false) | object-level keyframing — `rigforge_keyframe`'s sibling, on a transform rather than on pose bones. Everything is parsed **before** anything is applied, so a typo in `keys[7]` cannot leave `keys[0..6]` half done. Returns `{"object", "action", "created_action", "keys", "keys_set", "channels", "frame_range", "frames", "action_frame_range", "interpolation", "interpolated_points", "cleared_fcurves", "fcurves", "rotation_mode", "rotation_mode_changed", "units", "warnings", "seconds"}` |
| `set_material_emission` | `object?`, `strength` (0–1000), `color?` `[r, g, b]`, `frame?`, `interpolation?` (default **`CONSTANT`**) | the LED turning on. Ensures an emission-capable material, sets strength and colour, and keyframes both when a `frame` is given. Returns `{"object", "material", "created_material", "node", "node_type", "strength", "color", "frame", "keyed", "keyframed", "interpolation", "interpolated_points", "action", "fcurves", "notes", "seconds"}` |
| `render_animation` | `path` (`.mp4`), `frame_start`, `frame_end` (≤600 frames), `fps?` (1–60, default 24), `resolution?` (128–1920, default 640), `engine?` `eevee`\|`workbench` (default `eevee`), `objects?`, `view?` `iso`\|`front`\|`side`\|`top` | **read-only.** A temporary orthographic camera framed the way `render_preview` frames one, Blender's own ffmpeg writing an H.264 `.mp4`, every borrowed setting put back in a `finally`. Returns `{"path", "objects", "frame_start", "frame_end", "frames", "fps", "duration_s", "resolution", "view", "engine", "engine_requested", "container", "codec", "size_bytes", "framed_all_visible", "framed_over_frames", "bounds_mm", "ortho_scale_mm", "duration_ms", "honesty", "notes"}` |

**`location_mm` exists because every number in this toolchain is a millimetre.**
`plunger_plan` says the cap travels 2.1 mm; `{"frame": 8, "location_mm": [0, 0, -2.1]}`
is that sentence with nothing to convert and nothing to get wrong. `location` (metres,
Blender's own unit for a transform) still works, and giving both for one key is an
error rather than a merge.

**Emission keys are `CONSTANT` by default.** An LED is off and then it is on; it does
not fade up over seven frames. The parameter exists for the deliberate exception (a
heater, a charge indicator). Three ways into a material, in order of how little they
disturb: an emission node that is already there, a Principled BSDF's own emission
inputs (so an existing material keeps its look), or a new emission node. An object
with no material at all gets **`Forge Glow`** — a bare emission shader, because an LED
lens reads as a light and not as a lit surface.

**The camera frames the whole clip, not frame one.** The subject *moves*, which is the
point, so the bounds are the union over up to 8 sampled frames of the range — framing
on the first frame is how a plunger presses itself out of shot.

**Two Blender 5.0 traps, both load-bearing.** Video output is gated behind the new
`image_settings.media_type`: `file_format = "FFMPEG"` fails with *enum "FFMPEG" not
found* until the media type says `VIDEO`, and the restore therefore has to put
`media_type` back **before** `file_format` (the snapshot order makes that happen). And
the exact-path rule: with `use_file_extension` on and a path that already ends in
`.mp4`, Blender writes *that* file — without the extension it appends `0001-0048.mp4`
and hands the caller a path that does not exist.

**Undo:** `render_animation` is in `READ_ONLY_COMMANDS` for exactly the reasons
`render_preview` and `turntable` are. `animate_object` and `set_material_emission` are
deliberately **not**: keys and materials are the artist's work, and Ctrl+Z is what a
demo that went the wrong way needs.

**Measured on this machine (RTX 5070, Blender 5.0, `--background`):** 48 frames at
640 px on EEVEE in **52.8 s cold (1.10 s/frame) and 6.9 s warm (0.14 s/frame)** — the
gap is EEVEE's shader compilation, which the first render of a session pays for and
every render after it does not. 12 frames at 320 px on EEVEE: 13.9 s cold, 1.5 s warm.
12 frames at 320 px on Workbench: **0.8–1.9 s**, cold or warm, because Workbench
compiles nothing. Budget both ways when quoting a wait to the artist: the first demo of
a session is the slow one.

### Floor plans to prototype levels (Phase 19 — `build_floorplan` + `reconcile_floorplan`, `tools/floorplan.py`)

The artist's ask, near-verbatim: *"I draw rooms, walls, doors, and give it a label key —
washer/dryer here — and I want a simple 3d level created with the walls and doors, and
washer/dryer as rectangles to help map out. Then from there we can edit a shape and make
it more complex. We should be able to do prototypes; on the floor plans and basic 3d
rooms we should allow modifications on drawings without full regen; we support
additions."* One command, one plan file, and **the second call only builds what changed**.

| type | params | does |
|---|---|---|
| `build_floorplan` | `plan` (the `floorplan.json` object itself, resolved values, millimetres), `collection?` (default `Floorplan`), `mode?` `update`\|`rebuild` (default `update`), `floor?` (default **true**) | materialises the plan into scene objects named `FP:<id>`, and on every later call **diffs** the plan against what is already there. **Not read-only** — the registry pushes `Forge: build_floorplan` and one Ctrl+Z takes the whole diff back. Returns `{"collection", "built", "updated", "deleted", "unchanged", "kept": [{"id", "object", "why"}], "objects", "object_names", "walls", "openings", "fixtures", "floors", "rooms", "pieces", "mechanisms": [...], "bounds_mm": {"min","max","size"}, "dimensions_mm", "mode", "floor", "defaults_mm", "plan_version", "units", "honesty", "notes", "warnings", "seconds"}` |
| `reconcile_floorplan` | `plan` (the same resolved object), `collection?` (default `Floorplan`), `floor?` (default **true**, and pass the same one the level was built with) | the **return channel**: measures every `FP:` object in the collection against what Forge put there and hands back millimetres. **Read-only** — it is in `READ_ONLY_COMMANDS`, there is no write in it, and the headless suite proves it on `as_pointer()` identity *and* on the world matrices. Returns `{"collection", "objects", "plan_entries", "clean": [ids], "moved": [...], "resized": [...], "stale": [ids], "deleted_in_scene": [{"id","kind","object","was"}], "candidates": [...], "unabsorbable": [{"id","object","why"}], "tolerance_mm", "floor", "plan_version", "units", "honesty", "notes", "warnings", "seconds"}`, where a `moved`/`resized` record is `{"id", "kind", "object", "measured", "was", "changed": [fields], "moved_mm", "mesh", "confidence": "measured"}` |

The add-on does **not** read images and does not talk to the geometry service: the plan
arrives as data over the socket with every value already resolved, and this command's
whole job is to turn numbers into objects and — far more importantly — to *keep* turning
an edited set of numbers into the same objects.

**The id is the whole contract.** Every room, wall, opening and label carries a stable id,
and that id names the object: `FP:wall-01`, `FP:wd-01`. Nothing else identifies anything.
So a rebuild is a **diff against ids**, never a regen:

- an **unchanged** entry is not touched *at all* — same object, same mesh datablock, same
  materials, same transform, same custom properties. The code path for it is a `continue`,
  and the suite proves it with `as_pointer()` rather than with the report;
- a **changed** entry rebuilds only its own object (the object survives and its mesh
  datablock is swapped, so modifiers and parenting the artist added survive with it);
- a **new** id creates; a **missing** id deletes — and only ever an `FP:`-prefixed object
  inside the named collection, because deleting something Forge did not build would be the
  one unforgivable bug in a command whose job is to leave things alone.

**"Changed" is a fingerprint, not a guess.** When an object is built, a content hash of its
entry's fully resolved values goes into `obj["forge_fp_hash"]`, alongside
`forge_fp_id`, `forge_fp_kind`, `forge_fp_verts` and `forge_fp_dims_mm`. The hash is
canonical JSON (sorted keys, no whitespace) so reordering the plan's keys is not a change,
and `BUILD_VERSION` is hashed in too, so the day the geometry in this module changes every
existing object correctly reads as stale. Defaults are resolved *into* each entry before
hashing, which is why raising `defaults.ceiling_mm` rebuilds every wall and leaves the
floors and the fixtures untouched — asserted by test, on allocation identity.

**A promoted placeholder is never clobbered.** Promotion is one-way: once the artist has
elaborated a slot, the plan keeps its footprint as the size contract but the geometry is
theirs. Before rebuilding *or deleting* anything, the object is asked whether it still
looks like what Forge built — `obj["forge_fp_keep"] = True` set by hand is an
unconditional hands-off, a vertex count that no longer matches is an edit, dimensions
off by more than **0.5 mm** (which is what catches a scale in the viewport) is an edit,
and an `FP:` object with no fingerprint at all was not Forge's to begin with. Any of those
and the entry is **skipped**, listed under `kept` with a sentence saying which object and
why, and warned about. Placement is the exception on purpose: the plan owns *where* a
placeholder stands, the artist owns *what shape it is*.

**`mode: "rebuild"` is the escape hatch, and it still honours `forge_fp_keep`.** It throws
the greybox away and builds it again — that is what it is for, and the notes say so out
loud — but an explicit marker outranks a mode.

**`reconcile_floorplan` is the return channel, and it exists because the protection above
is one-way.** *"I should be able to manually edit and forge should be aware of my
changes."* `build_floorplan` detects a hand edit and skips it, so the plan never finds out
what the artist did — and the detection has a hole the size of the viewport, because it
never looks at an object's **transform**: a wall dragged two metres north keeps its
fingerprint, reads as unchanged, and is put back where the plan says the next time its
entry changes. A wall *deleted* comes straight back on the next build, which is what
happened to `FP:wall-living-kitchen`, twice. So this command measures and hands back
millimetres: a wall's centreline, thickness and height; a fixture's footprint centre, size
and rotation (a right angle comes back **exact**, anything else comes back as the real
angle); a slab's outline bbox. `service.absorb_reconcile` decides what to do with them —
the add-on measures and never edits the plan.

**The reference is what Forge PUT there, not what the plan says.** `_stamp` writes
`forge_fp_loc_mm` and `forge_fp_rot_deg` beside the fingerprint, and reconcile compares
against those: the plan can have moved on since the build, and reading "the scene
disagrees with the plan" as a hand edit would absorb the artist's own plan edit straight
back out again. An entry whose plan changed since it was built is reported as **`stale`**
(a rebuild waiting to happen, not a measurement); an entry that is stale *and* has been
touched is **unabsorbable**, with a sentence saying to build first and reconcile after,
because Forge will not guess which of the two edits was meant.

**What it refuses to describe, it names.** A tilted or sheared transform, an object lifted
off z = 0, a mesh that is no longer a plain box (a **sculpt**, which is a promotion —
the reason says `forge_fp_keep`), an `FP:` object that is not a mesh: each comes back under
`unabsorbable` with the reason, never rounded into the nearest field that would take it. A
mesh edited in Edit mode but still one axis-aligned 8-corner box is measured like any other
resize, marked `"mesh": "edited-but-still-a-box"`. Two refusals guard the deletion path,
which is the one with teeth now that deletions are absorbed: a collection that **does not
exist**, and a collection with **no `FP:` objects in it at all** — because the honest answer
to the second would be "every entry in your plan has been deleted", and acting on that
would empty the plan of a level that was built into a different collection. An object found
in the file but *outside* the named collection is reported as outside it, never as a
deletion.

**Geometry is boxes, and the openings are cut by construction.** A wall is built in its own
frame (x along the centreline from `from_mm`, y across, z up) as the solid pieces left
over: a full-height pier between openings, a sill under a window, a header over anything
that does not reach the ceiling. No Boolean modifier — it is exact (no coplanar-face
lottery, no solver to time out) and fast enough that rebuilding a wall per keystroke is
free. A door leaves a header above and nothing below; a window leaves both; a `gap` runs
floor to ceiling and splits its wall into two separate piers. Rooms get an optional slab
hanging **below z = 0** so the walls stand on it (concave rooms are tessellated, so an L
gets a real L). Labels become boxes at their footprint, spun about Z by `rotation_deg`.

**Two conventions the schema leaves open, decided here and said out loud:** an opening's
`at_mm` is its **centre** measured along the wall from the `from_mm` end (pass `start_mm`
instead for the near edge — both at once is refused, because they disagree by half a
width), and a label's `footprint_mm` `[x, y, w, d]` puts `[x, y]` at the **centre** of the
box (`"anchor": "corner"`, or `defaults.label_anchor`, switches it).

**Materials are three flat colours by kind** — `Forge FP Wall` (grey), `Forge FP Floor`
(darker), `Forge FP Fixture` (accent) — made once and shared by every object. A greybox
with a hundred materials is not a greybox. Each is given a `diffuse_color` as well as a
Principled base colour, because a prototype level is looked at in Workbench far more often
than it is rendered.

**A door with a `swing` comes back as a Phase 17 mechanism record** — `{"joint_type":
"revolute", "axis": [0, 0, 1], "origin_mm": <the hinge edge on the centreline>,
"range_deg": 90, "direction": ±1, "swing", "hinge", "width_mm", "height_mm",
"actuated_by"}` — which is the same handful of fields URDF wants. It is **data in the
report only**: no leaf object is built and nothing is rigged, and the record says so.
`direction` is the product of which way it swings and which end it hangs from, because a
right-hung door swinging in turns the opposite way about +Z from a left-hung one.

**Refusals are sentences**, and the plan is parsed *in full* before one thing in the scene
is touched — a plan that is going to be refused is refused before half a level exists.
Duplicate ids (ids name objects, so they have to be unique across rooms, walls, openings
and labels), an opening wider than the wall it is in, a label with no footprint, an opening
that hangs off the end, two openings overlapping, an unknown opening kind or mode (with the
`difflib` near miss), a zero-length wall, a room with fewer than three corners or no area,
a plan in units that are not millimetres, an id too long for a Blender name, and both
`at_mm` and `start_mm` at once.

**Said in every report** (`honesty`): this is a prototype greybox at real sizes and not a
construction drawing. Nothing is framed, structural, insulated, code-compliant or
load-bearing; the walls are solid pieces that meet face to face where they touch, which
makes them greybox geometry rather than a printable solid; and every dimension is only as
true as whatever calibrated the plan.

**Measured on this machine (Blender 5.0.1, `--background`):** a two-room flat — two slabs,
five walls, one door, one fixture — in well under a millisecond; **40 walls with 40 doors
in 0.047 s**, and re-diffing that same plan (40 hashes, nothing touched) in under a
millisecond.

### Silhouette fitting (Phase 18b — `fit_to_silhouette`, `tools/silhouette.py`)

The artist's ask, verbatim: *"take an image and use the silhouette to map out or move
the sculpt to match that silhouette, and do multiple sides if provided, so front and
side."* One command, one or more reference pictures, one per orthographic view.

| type | params | does |
|---|---|---|
| `fit_to_silhouette` | `object?`, `views` (`[{"image": <path>, "axis": "front"\|"back"\|"side"\|"left"\|"right"\|"top"\|"bottom", "threshold"?: 0–1}]`) **or** the shorthand `front_image` / `side_image` / …, `strength?` (0–1, default 1), `iterations?` (1–12, default 3), `smooth?` (0–1, default 0.5), `falloff?` (0–4, default 0), `symmetry?` (false \| true \| `X`\|`Y`\|`Z`), `fit?` `height`\|`width`\|`bbox` (default `height`) | deforms the mesh until its projected outline follows the reference in every view given. **Not read-only** — it edits the artist's mesh, so the registry pushes `Forge: fit_to_silhouette` and one Ctrl+Z puts it back. Returns `{"object", "vertices", "faces", "views": [...], "strength", "iterations", "iterations_run", "smooth", "falloff", "fit", "symmetry", "symmetry_plane_mm", "applied", "moved", "moved_fraction", "max_displacement_mm", "mean_displacement_mm", "displacement_by_axis_mm", "constrained_axes", "untouched_axes", "iteration_max_mm", "dimensions_before_mm", "dimensions_after_mm", "bounds_before_mm", "bounds_after_mm", "shape_keys", "modifiers", "method", "honesty", "notes", "warnings", "seconds"}` |

Each entry of `views` comes back as `{"axis", "resolved_axis", "image", "image_size",
"plane", "depth_axis", "constrains", "mask": {"source" (a tiered claim), "threshold",
"coverage", "pixels", "bbox_px", "background_uniformity"}, "confidence",
"confidence_reasons", "fit", "scale_mm_per_pixel", "centre_mm", "target_size_mm",
"mesh_size_before_mm", "iou_before", "iou_after", "iou_gain", "shape_iou_before",
"shape_iou_after", "outline_error_before_mm", "outline_error_after_mm",
"rays_unresolved", "grid", "grid_cell_mm"}`.

**A view only moves the two axes it can see.** `front` moves X and Z, `side` (= `right`)
moves Y and Z, `top` moves X and Y, and the depth axis of a view is the one thing that
view has no opinion about — a front-only fit reports `displacement_by_axis_mm["Y"] == 0.0`
and the Y coordinates come back bit-identical, asserted by test. An axis two views both
constrain (Z, for front + side) takes the **average** of what they ask, which is what
makes a front and a side reference agree on one ellipsoid instead of fighting.

**The warp is a smooth radial scale field, not a projection onto a curve.** In each
view's plane, both outlines are measured the same way by the same function — a ray
marched out from the shared centre until it leaves the silhouette, 256 angular bins —
the reference off its thresholded picture and the mesh off a **rasterised projection of
its own faces**. The ratio of the two radii at an angle is how much the mesh must grow
or shrink there, and every vertex moves along its own radius by that ratio, interpolated
between bins. So the outline lands on the reference *and* everything inside moves by the
same proportion: the ridge of a nose stays where the artist put it relative to the jaw.

**Why the mesh outline is rasterised rather than read off the vertices.** Binning
projected vertices by angle and taking the farthest one sounds equivalent and is not: a
bin whose farthest vertex happens to sit slightly inside the true outline reports a
radius that is too small, and every vertex at that angle is then pushed too far — a 10%
lump on a UV sphere. The surface has an outline whether or not a vertex landed on it, so
the surface is what gets measured (384 cells across a doubly-padded frame ≈ 0.5% radial
precision).

**`smooth` smooths the DISPLACEMENT, never the mesh.** The Laplacian passes run over the
per-vertex movement vectors before any of them are applied, so spikes in a noisy
reference are faired out and a 200 000-vertex sculpt keeps every pore it came in with.
Measured: a deliberate 350 mm spike still stands 470 mm proud after a fit at `smooth=1.0`.

**`falloff` is the knob for artists who want only the rim to move.** At `0` (default) the
whole cross-section scales together; at `3` the interior barely moves and the outline
still lands. Measured on the fixture: interior vertices moved **98.3 mm at falloff 0 and
9.7 mm at falloff 3**, with the outline IoU 0.988 and 0.989 respectively.

**Alignment is bbox-to-bbox, and `fit` says which extent is the anchor.** `height` (the
default) scales the reference so its silhouette is as tall as the mesh's projection, then
centres it — height because the vertical axis is the one front and side views share,
which is what lets two references agree. The command changes the shape of the sculpt, not
where in the world it stands.

**Every view is measured, before and after.** `iou_before` / `iou_after` are the overlap
of the mesh's projected silhouette with the reference, on a **fixed grid against a frozen
target**: the alignment, scale, centre and reference profile are all computed once,
against the mesh as it was before the first iteration, so the thing the fit is graded on
at the end is the thing it was aimed at at the start. Recomputing the alignment from the
deformed mesh would move the goalposts. `shape_iou_*` is the same overlap with both
silhouettes cropped and fitted to one square (`verify._normalise_mask`, verbatim), so
"the proportions are wrong" and "the shape is wrong" stay two findings.

**Masks come off `verify.py`'s own machinery** — one measurement, one implementation. An
alpha channel is the mask exactly (tier `measured`); otherwise it is every pixel further
than `threshold` from the median border colour, which is a luminance threshold when the
border is white paper (tier `heuristic`, and the report shouts PLAIN BACKGROUND). A stray
pixel in a corner is **warned about, never silently cropped** — trimming it would clip a
foot.

**Said in every report** (`honesty`): a silhouette fit matches an outline. Depth the
reference never had is not invented, interior form is scaled with the outline rather than
rebuilt, and a concavity a ray from the centre cannot see — the gap between two legs, the
inside of a horseshoe — is approximated by the outline it can see.

**Measured on this machine (Blender 5.0.1, `--background`):** a 482-vertex sphere fitted
to a 2:1 ellipse in **0.14 s**, IoU **0.497 → 0.990**, mean outline error 313 mm → 2.5 mm.
Front **and** side on an 8 066-vertex sphere, 4 iterations, in **0.33 s** — the whole
thing is numpy, and the rasteriser is the expensive half.

**Scrappable by parameter, all of it:** `strength=0` is an exact no-op that still returns
the measurement (nothing is written to the mesh at all, and the report says so), `smooth=0`
turns the fairing off, `falloff` chooses between warping the whole section and moving only
the rim, `symmetry` chooses whether a lopsided photograph is allowed to make a lopsided
sculpt (measured: mirror residual 61.4 mm off, 0.00 mm on), and `iterations` trades time
for accuracy.

### The geometric gate — `verify_design` and `turntable` (`tools/verify.py`)

**Renders judge beauty; this judges truth; both gates must pass.** The reason the
second gate exists is measured rather than felt: render-based judging systematically
rewards visual impact over downstream utility (123 k human votes; +144 ELO for
textured over untextured, and *the same model* scoring 78 ELO higher presented as a
splat than as a mesh), and ~26 % of paired VLM judgements reverse when the two
candidates swap presentation order. A loop that only looks can be fooled by
prettiness, which is exactly the failure mode `render_preview` alone leaves open.

`verify_design` composes ONE scored report over six axes. Each axis carries a
`status` (`pass` / `attention` / `reported` / `not_applicable`), a `summary`
sentence, a `tier`, and a `detail` block.

| axis | what it measures | tier |
|---|---|---|
| `defects` | the whole of `mesh_diagnose`, **composed in verbatim** — clipping, unsealed edges, zero-area faces, density hotspots, scale anomalies, each with a place in millimetres. Not reimplemented: one measurement, one implementation | `measured` |
| `poly_budget` | `face_count` against a target, with the ratio. Default 15 000 for `game` (RigForge's own `PLATFORM_TARGETS["desktop"]`), ungated for `print`/`any` unless a `poly_budget` is passed | `measured` |
| `uv` | island count (connected faces sharing matched loop UVs — Blender's own definition, so the number matches the UV editor), flipped faces (the minority winding, so an all-negative layout is a convention and not a defect), degenerate faces, out-of-bounds loops (legal for UDIM, so reported not failed), area distortion (worst texture-density ratio against an even layout), total coverage — plus an **overlap estimate** | `measured`, except `overlap` |
| `symmetry` | mirror residual on X/Y/Z: mean / p95 / max distance from a mirrored vertex to the nearest real one, in millimetres, plus the same as a fraction of the bounding diagonal | `measured`; `near_symmetric` is `heuristic` |
| `loops` | how many edge loops cross each deformation zone, wanted ≥ 3. Joints come from an **armature** when there is one and from the **RigForge tag boundaries** when there is not | `heuristic` throughout |
| `silhouette` | IoU of the front silhouette against a reference image, plus aspect and centroid deltas | `measured`; the reference mask is `measured` from alpha, `heuristic` from a threshold |

**Credibility tiering, the pattern taken verbatim from the competitive sweep.** Every
leaf in the report is a `{"value", "tier", "note"?}` claim, and there are exactly two
tiers: `measured` (a computed number with a definition — if it is wrong, the code is
wrong) and `heuristic` (a number that took a judgement call — real information, wrong
to quote as fact). The distinction is load-bearing: a silhouette IoU thresholded off
somebody's photograph and a face count are both numbers and are not both facts, and a
report that presented them alike would train the reader to trust the wrong one. The
harness walks the whole report structurally and asserts that *every* claim carries a
legal tier, and that both tiers are actually used.

**Profiles.** `for` does not filter the report — it decides which axes are **gated**
(may say `attention`) and which are merely reported:

* **`game`** gates `defects`, `poly_budget`, `uv`, `loops`, `silhouette`.
* **`print`** gates `defects` and `silhouette` only. It deliberately grows **no**
  second opinion about bed fit, wall thickness or overhangs: those are
  `partforge_check` / `check_model`'s question against the real printer profile, and
  a weaker duplicate computed from a bounding box would be worse than none. The
  profile note points at them by name.
* **`any`** (the default) gates the two universal axes.

`symmetry` is never gated in any profile. Its `status` is `reported` and it carries
`judged: false`, because a swept tail, a hand on a hip and a symmetrize that did not
take all produce a large residual and only the artist can tell them apart.

**Silhouette IoU, and every approximation in it.** The object is rendered front-on
with `film_transparent`, so the subject mask is the alpha channel — exact, no
thresholding. The reference's mask is its own alpha when it has one (also exact), and
otherwise a threshold against the **median border colour**, which assumes a plain
background. Both masks are then cropped to their subject and fitted into the same
128 × 128 square preserving aspect, so the IoU is about **shape** and not about how
far away the photographer stood; aspect and centroid deltas are reported separately,
from before the normalisation, so "the proportions are wrong" and "the shape is wrong"
stay two different findings. How plain the background actually was comes back as
`background_uniformity` and drives a `confidence` of `high` / `medium` / `low` with
`confidence_reasons` in sentences — the report never assumes the assumption held.
The pass threshold is **0.85**, calibrated rather than picked: a circle inscribed in a
square scores π/4 = 0.785, so 0.75 would let a *sphere* pass as a *cube* — which the
harness measures.

**Edge loops, and why they are heuristic all the way down.** Vertices inside a band
around each joint are projected onto the bone axis and clustered along it; each
cluster counts as one loop. The clustering tolerance comes from the mesh's own median
**edge length** (35 % of one), not from the gap statistics — on ring topology the gaps
*within* a loop are ~0 and the gaps *between* loops are one edge, so an edge-relative
tolerance separates them exactly, while a median-gap-relative one collapses every limb
to a single loop. It is exact on ring topology and increasingly approximate on
anything else, so it never claims to be measured.

The tag-boundary path handles the case Forge's own tagging actually produces: two
tags **share** the seam ring (a face's tag goes to all of its vertices, so the faces
either side of a seam both claim it), and reading only edges that *cross* between
disjoint tags — the obvious implementation — finds nothing at all. Both are read.

`turntable` is the judging rig the protocol research settled on: **24 views at
256 px**, stitched into one contact sheet reading left-to-right, top-to-bottom. One
image rather than N files because a VLM reads one image far more cheaply, and because
the whole point is comparing the views *against each other*. Every tile is framed with
a single ortho scale derived from the **bounding sphere** — the only fit that does not
change as the camera orbits — so anything that changes between tiles is the model
changing and never the camera.

Both commands are in `READ_ONLY_COMMANDS`. `verify_design` measures; `turntable`
borrows the render settings, the colour management and a camera and puts every one of
them back, exactly as `render_preview` does, and the silhouette render goes to the
system temp folder rather than next to the artist's picture.

**Caps, said out loud rather than applied silently.** The UV island walk is skipped
above 200 000 faces (every other UV number is vectorised and still runs, and the
result says which); the symmetry KD-tree subsamples above 120 000 vertices, which can
only *overstate* a residual, never understate it — the safe direction for a number
nobody is allowed to fail on.

### RigForge commands (Phase 3)

| type | params | does |
|---|---|---|
| `rigforge_list_tags` | — | every `tag_*` vertex group with its vertex and face counts, plus how many faces are untagged |
| `rigforge_tag` | `tag`, `faces` \| `use_selection`, `replace?` | assigns the vertices of those faces to `tag_<Name>` at weight 1.0 |
| `rigforge_untag` | `tag`, `faces?` / `use_selection?`, `include_shared?` | drops faces from a tag; with no faces given, deletes the tag |
| `rigforge_manifest` | `action` `save`\|`load`\|`get`, `path?`, `archetype?`, `motion_notes?`, `name?`, `create_missing_tags?` | reads/writes `character.json` |
| `rigforge_retopo` | `target_faces?`, `platform?`, `lods?`, **`unwrap?`** (default true), **`lod_budgets?`**, **`protect_seams?`** (Decimate fallback only), `seams_from_tags?`, `margin?`, `angle_limit?`, `bake_normals?`, `bake_resolution?`, `bake_path?`, `voxel_size?`, `keep_original` | the stage 2 pipeline in canonical order (retopo → unwrap → bake → LOD); returns object names, face counts, `uv`, `lod_reports`, `simplifier`, `budget` |
| `rigforge_auto_uv` | `seams_from_tags?`, `margin?`, `angle_limit?`, `method?` | seams, unwrap, pack; returns island count and UV coverage. Marks the mesh as really unwrapped, which is what lets a bake run |
| `rigforge_status` | — | one-call overview: tags, archetype, motion notes, manifest path, derived meshes |

### RigForge commands (Phase 4 — rig and Godot export)

| type | params | does |
|---|---|---|
| `rigforge_metarig` | `object?`, `archetype?` `auto`\|`biped`\|`quadruped`\|`custom`, `modules?`, `spring_chains?`, **`preset?`**, **`joints_file?`**, **`joints_weight?`**, **`joints_tolerance?`**, **`joints_disagree_band?`**, **`joints_axis_up?`** | builds a Rigify metarig and fits it to the tags; ear/tail tags become bone chains. With `joints_file`, a neural detector's predicted joints refine that fit (see [The rigging bridge](#the-rigging-bridge-phase-4-stage-4a-joints_file)). Returns `metarig`, `bone_count`, `mapping` (tag → bones), `chains`, `landmarks`, `joints`, `warnings` |
| `rigforge_generate_rig` | `metarig?`, `mesh?`, `parent_with_weights?`, `cleanup?`, **`max_influences?`**, **`band?`**, **`spring_chains?`** | Rigify generate → automatic weights → per-tag weight cleanup. Returns `rig`, `weighted`, `cleanup_report`, `spring_chains`, `warnings` |
| `rigforge_weights` | `object?`, `action` `report`\|`cleanup`\|`normalize`, `max_influences?`, **`rig?`**, **`band?`** | per-bone influence counts and the two numbers that mean trouble; or re-runs the rules |
| `rigforge_export_godot` | `rig?`, `meshes?`, `path`, `actions?` `all`\|`[names]`, `root_motion?`, `deform_only?`, `godot_import_script?`, **`lods?`** `auto`\|`manual`, **`frame_step?`**, **`unit_scale?`** | bakes every action onto the deform bones, strips the control rig, writes glTF (**with tangents**) + a Godot `.gd` import helper. Returns `path`, `actions`, `deform_bones`, `files`, `lods`, `lod_chain`, `tangents` |
| **`rig_check`** | `rig?`, `mesh?`, `poses?` `extreme`\|`quick`\|`full`\|`[angles]`, `joints?`, `max_poses?`, `intersections?`, `weight_floor?`, `intersection_face_limit?` | the **deformation harness**: poses every limb, spine and neck joint to its extremes and measures volume loss, new self-intersections and twist collapse on the evaluated mesh. Returns per-joint numbers with verdicts, an overall `gate`, the `thresholds` that judged them, and `pose_restored`. See [The deformation harness](#the-deformation-harness-rig_check) |

Parameters in **bold** are additive refinements beyond `docs/architecture.md`'s Phase 4
sketch; every one has a default that reproduces the sketch's behaviour.

### RigForge commands (Phase 5 — cloth and animation)

| type | params | does |
|---|---|---|
| `rigforge_cloth` | `object?`, `tags` \| `use_selection`, `name?`, `offset_mm?`, `thickness_mm?`, `preset` `cotton`\|`leather`\|`heavy`, `output` `skin_tight`\|`shapekeys`\|`bones`, `frames?`, `collision?`, **`self_collision?`**, **`subdivide?`**, **`rig?`** | grows a garment from the tagged faces, offsets, thickens, weights it to the rig, and (for `shapekeys`) settles it with a cloth sim baked into a `Settled` shape key. Returns `garment`, `output`, `shape_keys`, `physics`, `weights`, `sim`, `warnings` |
| `rigforge_action` | `action` `new`\|`list`\|`delete`\|`duplicate`\|`rename`\|`push_nla`, `name?`, `source?`, `rig?`, `loop?`, **`new_name?`** | the Godot action library, with the `-loop` convention enforced in both directions. Every call returns the whole library in `actions` |
| `rigforge_keyframe` | `rig`, `action`, `keys`, `interpolation?`, `clear?`, **`loop?`**, **`fk_switch?`** | batch keyframing on control bones. Returns `action`, `keys_set`, `frame_range`, `bones`, `rotation_modes`, `fk_switched` |
| `rigforge_retarget` | `source_path` (.bvh/.fbx you supply), `target_rig`, `action_name`, `mapping?` `auto`\|`{src: dst}`, `loop?`, `scale?`, **`frame_step?`**, **`replace?`**, **`fk_switch?`** | imports the clip into a temp collection, maps bones by name heuristics, bakes onto the FK controls, deletes the import. Returns `action`, `mapped`, `unmapped`, `frames`, `scale` |

Parameters in **bold** are additive refinements beyond the Phase 5 sketch; every one has a
default that reproduces the sketch's behaviour. Nothing in Phase 5 downloads anything:
retargeting reads only files you point it at, with importers that ship inside Blender.

Full behaviour is documented under [RigForge panel](#rigforge-panel) below.

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

### Additive protocol extension (Phase 6b): segments straight into `load_meshes`

`meshes` entries may now be `/segment`'s **own** segment objects — geometry under `mesh`
rather than at the top level — and a top-level `plate` (the service's whole plate object)
places each one by name:

```jsonc
{"type": "load_meshes", "params": {
   "meshes": [{"name": "segment_1", "kind": "segment",
               "mesh": {"vertices": [[x,y,z], ...], "faces": [[i,i,i], ...]}}],
   "plate": {"items": [{"name": "segment_1", "position_mm": [5.0, 5.0, 0.0],
                        "rotate_deg": 0.0}]},
   "replace": true}}
```

Entries that already look like `load_mesh` specs are passed through untouched, and a
per-entry `plate` still wins over the plate table — so nothing that worked before changes.
This is what lets a saved flow wire `/segment` into Blender with a plain reference
(`"{{steps.0.result.segments}}"`) and no reshaping code in between; the MCP tool
`partforge_load_segments` does that reshaping in Python, and now does not have to.

A segment list with no geometry in it gets the honest error rather than a shrug: *"None of
these entries carry geometry … ask for it with `"include_mesh": true`"*.

### Additive protocol extension (Phase 6c): `load_reference`

The artist's reference picture, in the viewport, to model **against** — never to trace.

```jsonc
{"type": "load_reference", "params": {
   "path": "C:\\Users\\you\\Pictures\\bowl-sketch.png",
   "view": "front", "size_mm": 200, "name": "Ref-front"}}
```

- **It is an EMPTY of type IMAGE**, not geometry and not a textured plane. It cannot be
  exported (`export_stl` refuses it by name), printed, remeshed or booleaned by accident,
  and the artist moves, scales or hides it like any other object.
- **Orientation.** An image empty draws in its own local XY plane facing local +Z, so each
  view spins that until the picture faces whoever is looking from it, with the top of the
  picture kept upright: `front` → `(90°, 0, 0)` so it faces −Y (Numpad 1), `side` →
  `(90°, 0, 90°)` so it faces +X (Numpad 3), `top` → `(0, 0, 0)`, the empty's own default,
  facing +Z (Numpad 7).
- **Offset.** The plane sits `offset_mm` (default **1 mm**) *away* from that viewer — front
  +Y, side −X, top −Z — so it never shares a plane with a model sitting on the origin and
  the two do not z-fight.
- **Size.** `size_mm` (default **200**) is the picture's **longer** side in millimetres,
  which is exactly what Blender's `empty_display_size` means for an image empty (the plane's
  maximum dimension). The shorter side follows the file's own pixel aspect, so a reference
  is never stretched, and both come back as `width_mm` / `height_mm`.
- **Opacity.** `use_empty_image_alpha` on with the object colour's alpha at **0.5**, and
  visible in both orthographic and perspective views — an opaque reference hides the thing
  you are comparing it against, and one that vanishes when you orbit is worse than none.
- **`name`** defaults to `Ref-<view>`. Loading the same name again **replaces** that
  reference (new image, size and rotation on the same object) rather than leaving
  `Ref-front.001` behind; a name already taken by a mesh is refused rather than clobbered.
- **`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`** — the same five the Assistant's attach field
  and the bridge accept. A folder, a missing file, a `.txt` and a `.png` Blender opens but
  finds no pixels in each fail with a sentence (and the empty image datablock is taken back
  out, so a retry after fixing the file is clean).

Mirrored by the MCP tool `load_reference`. The philosophy is docs/plan.md §3: the picture is
for extracting proportions, features and style intent into **parameters**. Geometry is
always built from those, never traced from pixels, which is what keeps a model editable with
real values.

### Additive protocol extension: `render_preview`

The assistant's **eyes**. Every other command in `tools/common.py` changes geometry; this one
only looks at it, and it exists because the assistant used to design blind — set the
parameters, run the checks, declare it done, never once seeing that the result was stiff,
sparse and flat next to the artist's reference. Claude Code's Read tool renders images, so a
PNG on disk closes that loop.

```jsonc
{"type": "render_preview", "params": {
   "path": "C:\\Users\\you\\AppData\\Local\\Temp\\forge-previews\\preview-001-iso.png",
   "objects": ["bowl"], "view": "iso", "resolution": 768, "shading": "solid"}}
```

- **Orthographic, fitted to the bounds.** A temporary camera is rotated to the view, the
  eight world-space corners of the targets' (modifier-evaluated) bounding box are projected
  onto its right/up axes, and `ortho_scale` is `2 × max(half-width, half-height) × 1.12`.
  Orthographic on purpose: the fit is exact arithmetic rather than a field-of-view guess, a
  `front` render stays measurable against a `front` reference, and the same part rendered
  twice is the same picture twice — which is what makes *"did my change help?"* answerable.
  A cube parked 5 m from the origin still comes out centred and filling the frame.
- **The three orthographic views are `load_reference`'s three exactly** — `front` looks along
  +Y (Numpad 1), `side` along −X (Numpad 3), `top` straight down (Numpad 7) — so a `front`
  render and a `front` reference are the same projection and line up 1:1. `iso` is the
  classic 45° orbit at elevation `atan(1/√2)` = 35.264°, where all three axes foreshorten
  equally.
- **Workbench clay by default.** `scene.display.shading` set to a single neutral grey subject
  on a darker grey ground (the two never merge, so the silhouette reads whatever colour the
  materials are), studio light, shadows, specular and **cavity on** — without cavity a
  textured band and a plain band render identically, which is exactly the mistake this
  command exists to catch. View transform is forced to Standard: AgX washes a clay render
  into grey soup. `shading: "material"` switches to EEVEE instead, and adds a temporary sun
  only if the scene has no light of its own; if EEVEE cannot run here (no GPU, remote
  session) it answers with a Workbench picture and a `note` saying so, rather than an
  apology.
- **`objects` frames those objects only.** Everything else renderable gets `hide_render` for
  the duration, recorded per object, so a scene where the artist had already hidden something
  comes back exactly as hidden as it was. Omit it and every *visible* mesh is framed, with
  `framed_all_visible: true` in the result.
- **It leaves nothing behind.** Camera and light datablocks are removed and the whole
  snapshot — `scene.camera`, engine, filepath, resolution, percentage, film transparency,
  overwrite/extension/stamp/border, image settings, `render_aa`, all eleven Workbench shading
  fields, colour management, and every `hide_render` flag — is restored in a `finally`, so a
  render that *fails* restores just as completely as one that succeeds.
- **It never touches the artist's viewport.** It renders through `bpy.ops.render.render`,
  not `render.opengl`, so it needs no VIEW_3D area and works identically in `--background`
  and in a live session. `scene.display.shading` (what the render uses) and a 3D view's own
  `space.shading` (what the artist looks through) are different properties; only the first is
  borrowed. The command never reads `space_data`, never walks the window manager's areas and
  never uses `temp_override`.
- **Read-only**, so looking never eats the undo step the artist wanted.
- A missing path, a folder, an unknown view or shading, a resolution outside 128–2048, a
  name that is not in the scene, and a scene with nothing visible in it each fail with a
  sentence. A path with no extension gets `.png`; the folder check happens first, so
  *"render into my renders directory"* never becomes a file called `renders.png`.

Mirrored by the MCP tool `render_preview`, whose report ends by telling the model to Read the
file — a preview nobody looked at is the same blind design with an extra tool call in front
of it.

### Additive protocol extension (Phase 7): `import_generated`

What meshgen (`127.0.0.1:8902`) wrote, in the scene, in a state the rest of Forge can work
on.

```jsonc
{"type": "import_generated", "params": {
   "path": "C:\\forge-models\\comfyui-output\\gecko_trellis2.glb",
   "name": "gecko", "repair": true}}
```

- **The repair is the default, and that is the whole point.** Raw image-to-3D output is
  never manifold — paper-thin walls, boundary edges, inconsistent winding (meshgen's own
  README measures a fresh 200k-triangle output as *9 boundary and 210 non-manifold edges*
  with 3586 of 3861 wall probes under the minimum). Nothing downstream — print checks,
  segmenting, Quadriflow retopo — works on that, so the import voxel-remeshes before
  handing anything back. `"repair": false` exists for looking at exactly what the model
  produced, and says so loudly in the result.
- **The voxel size is adaptive**, the same reasoning RigForge's retopo uses: solve
  `area / v²` for the face count to land on rather than fixing a millimetre value that only
  suits one scale, then clamp between 16 and 400 voxels across the longest axis. The target
  is the mesh's *own* density (capped at 200 000 faces), so a repair keeps roughly the
  detail that arrived. An explicit `voxel_size` (scene metres) overrides it.
- **One object comes back, not a hierarchy.** A `.glb` carries the scene graph it was
  written with — typically an empty for the glTF root holding the Y-up rotation. The meshes
  are joined, that transform is baked into the mesh, and the leftover scaffolding is
  deleted, so `dimensions_mm` is the truth for every downstream measurement.
- **Scale is not guessed.** The file's units come through as they are and the result reports
  `dimensions_mm`; nothing in a picture says how big the thing is, so the artist decides.
- `.stl`/`.obj`/`.ply` are refused here with a pointer to the Model box's **Import Model**,
  which is the button that does open them.

Mirrored by the MCP tool `generate_3d`, which is the whole job in one call (submit, follow
the stages, import, print-check). The command is also a legal flow step.

### Additive protocol extension (Phase 11): drawn base shapes

Two read-only samplers turn a curve the artist drew into the control points a `forge_lib`
helper takes. **Neither builds anything.** The points go back to the caller, who writes them
into a PARAMS script — which is what keeps a drawn shape *parametric* instead of turning it
into a mesh that can never change again.

```jsonc
{"type": "profile_from_curve", "params": {"curve_object": "VaseProfile", "points": 7}}
{"type": "outline_from_curve", "params": {"curve_object": "EarOutline", "points": 10}}
```

- **The drawing plane is detected, not assumed.** The axis the stroke spreads along least is
  the one the artist was looking down, so the other two are the plane: `XZ` (front — what
  Numpad 1 gives, and what everything in Forge names), `YZ` (side) or `XY` (top). Ties go to
  the front view. The result reports `plane`, `plane_normal` and `flatness_mm`, and a stroke
  that wanders more than 2% out of its own plane is flattened onto it *with a note saying
  so*.
- **`radius` is the distance from the world Z axis**, so the model's centre line is the blue
  vertical line at the origin. A silhouette drawn on the left of it is folded onto the
  right; one that crosses it is folded too, and the note says the drawing was a whole
  silhouette rather than one half.
- **Curvature extrema survive the simplification.** Reducing ~200 tessellated samples to 7
  control points by keeping every 30th one loses the widest point of the vase, which is the
  one point that makes it a vase. The reduction is the Douglas-Peucker split — repeatedly
  keep the sample furthest from the chord it should be on — so the shoulder, the waist and
  the rim are exactly what survives.
- **Bezier splines are evaluated, not read.** `mathutils.geometry.interpolate_bezier` walks
  each segment at the spline's own `resolution_u`, so the samples are the curve Blender
  draws. Poly points are themselves; NURBS goes through Blender's own tessellation
  (`to_mesh`), and if that fails the control polygon is used **with a note**.
- **A profile can only climb.** `z` must strictly increase (`soft_body` enforces it too);
  samples that double back downward are dropped and counted in the notes, because a body of
  revolution cannot overhang itself. `close_bottom` (default) drops the profile onto `z = 0`
  so the body stands on the plate, and reports `z_offset_mm`.
- **An outline must be closed** — cyclic, or with its two ends inside 2% of the drawing's
  size. An open one is refused with the two keys that close it (`A`, then `Alt+C`). The
  simplified loop is checked for self-intersection, since `silhouette_part` refuses one.
- **Too few points is not a refusal.** A three-point poly curve is resampled evenly along
  its own length up to the helper's minimum, and says it did that.
- Refusals name what to do instead: an object that is not a curve, a name that is not in the
  file, **two strokes in one object** (a silhouette is one stroke — picking one silently is
  how an artist ends up with a body they did not draw), a point count outside the helper's
  band, a stroke with no height.

Both are in `READ_ONLY_COMMANDS`: measuring a drawing changes nothing, and an undo step for
it would only bury the stroke itself.

### Additive protocol extension (Phase 11): the component convention

A design is rarely one object. It is a **core** — the dimensioned part, the thing that has
to be a named number of millimetres — and a handful of **proposals** hung off it. So:

| | |
|---|---|
| the collection | named after the project — `gecko-bowl` |
| the core object | named after the project — `gecko-bowl` |
| each proposal | `<project>-<component>` — `gecko-bowl-collar`, `gecko-bowl-ear-l` |

That is what makes *"scrap the collar"* a one-object `delete_object` with nothing else
depending on it, and what lets `merge_for_print` take a collection name and mean "everything
still visible in it". `forge.tools.common` holds the three helpers the convention is made
of: `component_name(project, component)`, `split_component_name(name)`,
`common_project(names)` (the longest shared run of dash-separated pieces — `["panel-core",
"panel-core-ear"]` is a project called `panel-core`) and `project_of(name, known_projects)`.
Names are capped at Blender's 63-byte object-name limit, the same as the part-object rule.

### Additive protocol extension (Phase 11): `merge_for_print`

The last step before the slicer. The core and the proposals the artist kept are separate
watertight solids the whole time they are deciding — which is right — and exactly wrong at
the slicer, where two overlapping solids are two objects with a seam between them.

```jsonc
{"type": "merge_for_print", "params": {"collection": "gecko-bowl"}}
```

- **Which pieces**, in this order: an explicit `objects` list; else a `collection` (every
  **visible** mesh in it, which is what makes "scrap it and merge what's left" two words);
  else the current selection; else the active object. Anything skipped is named in `notes`,
  never silently dropped. `resolved_by` says which route was taken.
- **The voxel size is the whole argument, and the default comes from the machine:**
  `nozzle / 2` = **0.2 mm** on the Centauri Carbon's 0.4 mm nozzle. Half the nozzle is two
  voxels across the narrowest bead the printer can lay down — anything the grid loses at
  that size is something the printer could not have printed. Finer buys nothing on the plate
  and costs quadratically (triangles grow as 1/voxel²); coarser is a real choice, and it is
  the artist's to make out loud rather than ours to make quietly.
- **The face count is predicted before anything is remeshed.** The joined mesh's own surface
  area over voxel² estimates the polygon count; over **1 000 000** the voxel is coarsened
  until it fits, and the trade is written into `notes` in words. (A 100 mm sphere is ~31 000
  mm² of surface — about 780 000 polygons at 0.2 mm — so the cap is not theoretical.)
  Measured against the real remesh, the estimate lands within about ±40%, which is all a
  size-picker needs. A voxel finer than 0.05 mm, or coarser than a quarter of the smallest
  dimension, is clamped with a note.
- **The originals are hidden, not deleted** (`keep_originals`, default true) — the eye icon
  in the outliner brings one back, and `keep_originals: false` deletes them and says so in
  `deleted`. The command is **not** read-only: it pushes `Forge: merge_for_print`, and
  Ctrl+Z takes the whole thing back, unhiding included.
- **`watertight_input_count`** says how many pieces were sealed on their own, and
  `watertight` whether the result is — closing unsealed pieces is half the reason the step
  exists, and a merged shell that is *still* open is a note pointing at `mesh_diagnose`.
- The merged object is left selected and active, the Model box is pointed at it, and
  `next` says `check_model` — because a merged shell is a new mesh nobody has print-checked.

### Additive protocol extension: `partforge_open`

Another addition; nothing in `docs/architecture.md` changes shape.

When the assistant *writes* a part (MCP `partforge_new_part`), the artist should not then
have to find the file and type its path into the panel. `partforge_open` does that for them:
it sets `scene.forge_partforge.script_path`, asks the geometry service for the script's
`PARAMS` schema and rebuilds the parameter collection — the same `read_script` →
`request_json` → `sync_schema` path the **Load Script** button runs, not a fork of it. The
handler runs on the main thread like every command, so the `/parse_params` call is
synchronous here rather than going through `run_async`; parsing builds no geometry.

```jsonc
{"type": "partforge_open", "params": {
   "script_path": "C:/.../projects/small-magnet-holder/part.py"}}
```

- **`object`** overrides the object name the panel will build into. Omitted, it is derived
  the way the MCP server derives it — the script's stem, or its *folder* name when the file
  is generically named (`projects/small-magnet-holder/part.py` → `small-magnet-holder`) — so
  the panel's Regenerate button rebuilds the object `partforge_generate` made instead of a
  second one. The name is only re-derived when the script path actually changes.
- **`keep_values`** keeps values already tuned in the panel for parameters that still exist,
  exactly as the operator's own flag does.
- **`params`** supplies the schema directly and skips the service call. Only the headless
  tests use it; it is what lets them prove the panel plumbing with no service running.
- Changing script also **clears the previous part's check rows, verdict, suggested
  segmentation and stats** — a FAIL left over from another part is worse than no row at all.
- Nothing is built: the mesh appears when something calls `/generate` (MCP
  `partforge_generate`, or the panel's Regenerate).
- Failures are clean errors and the panel is rolled back to the script it had: no
  `script_path`, a file that is not there, or a service that is not running all leave the
  panel exactly as it was.

### Additive protocol extension (Phase 15): project `.blend` files

Two more commands, and nothing in `docs/architecture.md` changes shape. They close a gap
the artist named: *"clicking a model should open the associated blender file."* Everything
Forge builds up to here is parametric — a script, a spec, an STL — and none of that is
where a **sculpt** lives, or a lighting setup, or six carefully placed reference empties.
Those live in a `.blend`, and a project folder had nowhere to put one.

Where projects are is `FORGE_PROJECTS_DIR` if it is set (the same variable the bridge and
the MCP server read — two names for one folder would be a bug that only shows up on the
machine that set it), else `<repo>/projects`. Installed from a zip there is no repo above
the add-on, and the commands say so rather than guessing at somebody's disk.

```jsonc
{"type": "save_project_blend", "params": {"project": "small-magnet-holder"}}
{"type": "open_project_blend",  "params": {"name": "small-magnet-holder", "confirm": true}}
```

**`save_project_blend`** → `wm.save_as_mainfile(..., copy=True)` into
`projects/<name>/<name>.blend`.

- **`copy=True` is the entire feature.** Without it Blender *retargets the session*: the
  file the artist has been pressing Ctrl+S on all afternoon silently becomes the project
  file, and their next save goes somewhere they did not choose. A tool that moves where
  your work saves to is a tool nobody should hand a sculpt. `bpy.data.filepath` is the same
  string before and after, the result hands it back as **`session_file`** so a caller can
  check rather than trust, and a Blender build whose `save_as_mainfile` has no `copy`
  option is **refused** rather than served — that build cannot keep the one promise this
  command makes.
- `project` omitted means the project the PartForge panel is pointed at
  (`projects/small-magnet-holder/part.py` → `small-magnet-holder`), so the panel button
  needs no arguments.
- `create` (default true) makes the project folder when it is not there — which is what
  gives a sculpt that never had a folder a home. The name goes through the same alphabet
  gate the bridge uses (`[A-Za-z0-9._-]+`, and never `.` or `..`), so it can only ever name
  a folder directly under `projects/`.
- Returns `{project, path, folder, size, mtime, exists, replaced, created_folder,
  object_count, objects, session_file, retargeted: false, changed, where}`.

**`open_project_blend`** → `wm.open_mainfile`, which throws the running scene away. So the
first call, when there is anything to lose, does **not** open:

- no file yet → an **error** naming the button that makes one (an open that cannot happen
  is a missing file, not a question);
- unsaved work and no `confirm` → `{"needs_confirmation": true, "would_lose": <sentence>,
  "object_count", "objects", "hint"}` and **nothing is touched**. There is no undo across a
  file load — Blender resets the stack — which is precisely why this is a confirmation and
  not a checkpoint;
- otherwise → the world is replaced and the result carries `{opened, discarded,
  session_file, object_count, objects, server_running, server_port, pump_survived}`.

"Anything to lose" is `bpy.data.is_dirty` **and** a scene with objects in it. The second
half is not padding: an empty scene has nothing to lose whatever the flag says, and asking
"are you sure?" about nothing is how a dialog trains someone to click through the one that
mattered. It also happens to be the only way the "do not ask" branch can be tested —
in `blender --background` `is_dirty` is **always true**, true at startup before anything has
happened and not cleared by an explicit save (measured on 5.0.1).

**Does the socket server survive the world being replaced?** Yes, and it is proved rather
than assumed on every run of `headless_projects.py`. The listening socket, its accept thread
and the module-level server singleton are plain Python and a file load does not touch them —
Blender reloads *data*, not the modules holding it. The main-thread pump is a
`bpy.app.timers` callback registered **`persistent=True`**, and persistence is exactly the
flag that survives `open_mainfile`. The suite registers a second, deliberately
non-persistent timer beside the pump and asserts that one is **gone** after the open while
the pump is still there — without that control, "it survived" could only mean "nothing was
ever cleared". Then it round-trips two more commands through the same socket. Belt and
braces regardless: the handler calls `server._ensure_pump()` after the open, which is a
no-op when the timer is still registered and the difference between a live add-on and a
dead port on any future build where persistence changes meaning.

Both commands are in `READ_ONLY_COMMANDS`, for opposite reasons: `save_project_blend` writes
a file and changes nothing in the session (the `export_stl` precedent), and
`open_project_blend` destroys the very undo stack it would be pushing onto.

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

## Forge Status row

`View3D ▸ N sidebar ▸ Forge ▸ Forge Status` — the very top of the tab, above the chat box,
because "I pressed Send and nothing happened" has a short list of possible answers and this
box is all of them:

| Row | Where it comes from | Down means |
|---|---|---|
| **Shapes** | `GET <service_url>/health` | the geometry service (8765) is not running — nothing can be built, checked or cut |
| **Assistant** | `GET <assistant_url>/health` | the bridge (8901) is not running — the chat box cannot send |
| **Picture to 3D** | `GET <meshgen_url>/health` | meshgen (8902) is not running — **the only row that may be down on a working machine**, because the models are an 18.5 GB download. A warning (not a failure) when it answers `models_missing`, with the first missing file named |
| **Blender link** | this process's own socket status, not a port probe | the command socket is stopped, so Claude cannot drive Blender; the port is shown either way |
| **Sign-in** | the bridge's `claude_cli.found` + `last_auth_error` | the Claude CLI is missing, or the last turn failed on sign-in (`open a terminal, type claude, run /login`) |

Only **Shapes** and **Assistant** turn the status line red. Picture-to-3D down reads as
"Ready. Not running: Picture to 3D." — a fact, not a fault — and **Start services** does not
count it as a failure to start either, because `start_forge.ps1` deliberately skips meshgen
when the models are not installed.

**Nothing is probed by spending money or a turn.** Sign-in is whatever the bridge already
knows from the last job (`last_auth_error`, additive on `/health`) — the CLI is never run
to test it. The refresh arrows do the two health calls on a worker thread and land the
answer through a timer, so a dead port costs a second of nothing, never a frozen draw.

**Start services** runs the repo's own `start_forge.ps1` hidden (PowerShell,
`CREATE_NO_WINDOW`), so the button and the double-click do exactly the same thing and
there is one file that decides how those processes launch. Ports are probed first: whatever
is already answering is left strictly alone, and the status line afterwards names what it
actually started ("Started Assistant.") or what refused to ("Shapes did not start. Open
the forge folder and run start_forge.cmd to see why."). Installed from a zip there is no
repo above the add-on, so the button says so instead of guessing.

The session cost the bridge reports rides along on the same poll and fills in the
Assistant box's footer — one call, two answers.

## Assistant panel

`View3D ▸ N sidebar ▸ Forge ▸ Assistant` — the first box you can type in, with only the
status row above it, because it is the entry point for someone who has never opened
Blender before. Everything below it stays exactly as it was; the assistant is a layer over
the manual panels, never a replacement.

**Three chips do the three jobs everybody asks for**: **Check print**, **Segment to fit**
and **Export STL**, in a row above the message field. A chip is not a special code path —
it types the sentence an artist would have typed ("Run the print checks on the current
part and explain anything that fails in plain words.") and sends it through the same
`/ask`, with the same attachment rules and the same queue. The reply reads the same as if
they had written it, which is how they learn what to ask for next time.

**Revert last AI action** sits under the status line. Every socket command pushes a named
undo checkpoint *before* it runs (`Forge: remesh`, `Forge: load_meshes` — see
[Undo checkpoints](#undo-checkpoints)), so this button is plain `ed.undo`: one press, one
thing the assistant did. Ctrl+Z in the viewport does exactly the same job; the button
exists for the artist who does not know that.

**A second message waits its turn instead of being refused.** Sending while a turn is in
flight used to be a 409; now the bridge queues one message, the panel says
`Queued — waiting for the current answer ...`, and it runs the moment the first finishes,
in the same conversation. A *third* message is refused, with a sentence saying one is
already waiting.

**One row picks how hard it thinks.** Directly above **Send** is a compact selector with
three choices, labelled for the trade-off rather than for the product: **Fast** ("quick
jobs: segmenting, exports, questions"), **Smart (recommended)** ("new parts, image
references — the balanced default") and **Deepest** ("tricky design work; slowest"). The
choice rides in the `model` field of every `/ask`, chips included, so it belongs to the
message rather than to a mode the panel is left in — and a message queued behind another
runs on the model it was sent with. Switching between messages is free and keeps the
conversation: the next turn still resumes the same session, just written by the model you
just picked, so you can spend a few Fast turns exporting and then hand the same thread to
Deepest for the part that is actually hard. A new scene starts on whatever
`Edit ▸ Preferences ▸ Add-ons ▸ Forge ▸ Assistant ▸ Assistant Speed` says (default
**Smart**); once you pick something in the sidebar, that scene keeps your pick. No
environment variable is involved — the bridge's `FORGE_ASSISTANT_MODEL` is only the
fallback for a message that named nothing.

**The footer says what this has cost.** `This session: $0.42` comes from the bridge's
`session_cost_usd` (summed over finished turns, zeroed by New Conversation), with this
turn's own cost and duration on the right. Nothing is shown before the first answer.

**Long replies are readable.** A reply too long for the sidebar is cut at 24 lines with
`... N more lines` under it and a magnifier button in its header: that opens the whole
message in a popup, wrapped at 96 characters. Numbered handoff lists ("press N → Forge tab
→ Segments box → set Radial to 4") routinely run past what a 40-character sidebar can
show, and half a set of instructions is worse than none.

Type what you want in your own words, press **Send**, and the answer lands in the chat
log above the field (last six exchanges, `You:` / `Forge:`). **New Conversation** (the
page icon) clears the log and tells the bridge to forget the session; the globe icon
checks that the bridge and the Claude CLI are both there; while a turn is in flight the
row becomes a **Stop** button.

**While it is thinking you can see what it is doing.** Under the busy indicator the panel
draws the last five lines of the bridge's activity list — the tools it called with their
arguments (`partforge_check: part.py`), a `thinking…` marker when it starts writing, and
snippets of the reply as it streams. That comes from `GET /job/<id>`'s `activity` field on
every poll (Phase 6b); the finished job keeps it, so the lines stay readable after the
answer lands. A run against an older CLI that streams nothing simply shows nothing —
never an error.

The panel itself does no thinking. It POSTs to the **assistant bridge** on
`127.0.0.1:8901` (`assistant/bridge.py` in this repo, started by `start_forge.cmd`),
which runs the Claude Code CLI headless in the repo with the Forge MCP tools allowed.
Same async shape as PartForge: the operator returns immediately, a worker thread does the
HTTP, and a `bpy.app.timers` callback lands the reply on the main thread. `urllib` only,
every request has a timeout, and every failure ends up in the panel's status line.

The context sent with each message is the active object with its size **in millimetres**,
a summary of the scene's objects, the current PartForge script path, the mode, the
Blender version and — since Phase 8 — **where the artist actually is**: the objects they
have selected (`selected_objects`, capped at 10 plus an "... and N more" line) and, when
they are in a sculpt or paint mode, the brush in their hand (`brush`: name, size,
strength, and which symmetry axes are on). All of it is built on the main thread before
the worker starts, every attribute read is guarded, and the brush is only collected in a
paint-like mode — a brush they are not holding is noise, and noise in the context is a
sentence the assistant will believe.

### Buddy mode — a teacher's eyes on the work in progress

At the bottom of the Assistant box is a small box with a **Check my work** button and a
**Look every N min** toggle.

**Check my work** gathers, in one press:

1. `capture_viewport` — a screenshot of what you are looking at right now: your angle,
   your shading, your overlays;
2. `render_preview` (3/4 iso, clean clay) — the same model lit so the form reads;
3. `mesh_diagnose` — clipping, unsealed edges, density hotspots and starved regions, each
   with a place in millimetres;
4. the workspace context — mode, selection, brush.

It then sends all of that through the ordinary `/ask` path, led by the fixed line
`[check-in] Look at my work and tell me what you notice.` with both image paths and an
instruction to Read them before answering. The system prompt's **## Checking their work**
section is keyed off that first line: one clause on what is working, then at most three
concrete things with where and the fix, short, and never a note it already gave you.

Both turns are marked as check-ins, so the chat log labels them **Check-in:** with an eye
icon rather than **Forge:** — an answer nobody asked for reads as a bug unless the log
says where it came from. The outgoing turn shows as the short sentence "Check my work",
not the forty-line payload.

**The timer is off by default, and every check-in costs one Claude turn — the toggle's own
tooltip says so, and so does a line under it.** That is not a detail: a background process
quietly spending money is the one thing that would make someone switch this off and never
switch it on again. The interval is 10 minutes by default and **cannot go below 5**.

Three things stop it costing a turn for nothing:

- **it skips while the assistant is busy** (or has a message queued), and asks again in 20
  seconds rather than forfeiting the whole interval;
- **it skips when nothing has changed.** A cheap fingerprint of the active mesh — its
  name, its vertex and face counts and a hash of its coordinates (the whole array, via
  numpy: about a millisecond on a 200 000-vertex sculpt, and it cannot miss a stroke) — is
  compared against the one from the last look. Unchanged means no turn, and the clock is
  pushed forward so it does not spin;
- **it carries the last check-in's own words forward.** The previous reply's first 300
  characters ride along as `You previously noted: ...`, with "Do not repeat it — say what
  is new, or that it is fixed." So the second note is never the first note again.

The timer is a `bpy.app.timers` callback that wakes every 20 seconds and checks the clock
itself, so changing the interval takes effect without toggling anything. It refuses to run
at all in `--background`. `buddy.should_check(now, due, busy, signature, last_hash)` is a
plain function with no `bpy` in it and is where both skips actually live, which is why the
headless suite can prove them in milliseconds instead of waiting ten minutes.

**Attach a picture** (Phase 6c). Under the message box is a **Picture** field: the folder
icon opens Blender's own file browser. Once a file is picked the row becomes a chip with
the filename and an **X** to clear it (a 90-character path in a 40-character sidebar tells
you nothing; the filename tells you everything). The path is validated before anything is
sent — it must exist and be a `.png`, `.jpg`, `.jpeg`, `.webp` or `.bmp`, and a folder is
called a folder — so a wrong path is a sentence in the status line rather than a turn spent
watching the assistant fail to open it. It rides in `context.image_path` with the **next
message only** and clears once the answer lands; re-attach to send it again. The image
itself never leaves the machine's disk: only the path travels, and the assistant reads the
file with its own Read tool. Ask it to `load_reference` the same picture and it appears in
the viewport as a half-transparent plane to model against.

With nothing listening the status line reads, exactly:

```
Assistant not running — double-click start_forge.cmd in the forge folder
```

which names the script the repo root actually ships. The bridge address is an add-on
preference (`Edit ▸ Preferences ▸ Add-ons ▸ Forge ▸ Assistant`), default
`http://127.0.0.1:8901`.

Full protocol, environment variables and the CLI command line: `assistant/README.md`.

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
5. **Save Scene to Project** → `save_project_blend` for the project the script path names,
   writing `projects/<name>/<name>.blend`. It saves a **copy**, so your own file (File ▸
   Save) is not moved — which is why this button can sit next to Regenerate without being
   frightening. It is how a sculpt, a lighting setup or a set of placed reference empties
   gets a home; the web UI's Library then shows a scene-file chip on that project's card
   and its **Open** button loads it.

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

## Model box — downloaded and imported models

`View3D ▸ N sidebar ▸ Forge ▸ Model (downloaded / imported)` — under PartForge, because it
answers the same two questions about geometry Forge did not generate.

1. **Import Model** opens Blender's file browser for `.stl` / `.obj` / `.ply` and brings the
   file in scaled by the **File is in** setting — *Millimetres* by default, which is what
   print files are, so a 20 mm widget arrives 20 mm across instead of 20 metres. The status
   line names the object, its size and its face count, then says what to press next.
   (Anything already in the scene works too: select your own sculpt and skip this step.)
2. **Check imported model** reads the object's *evaluated* mesh (modifiers applied, world
   transform baked in), scales it to millimetres and POSTs it to the geometry service's
   `/check_mesh`. The answers land in the **Print Checks** box above — the same rows, the
   same icons — because there should not be two kinds of check to learn.
3. **Segment imported model** POSTs the same mesh to `/segment_mesh` with the joint and
   mode from the **Segments** box, and loads the pieces laid out on the plate, exactly as
   the parametric Segment button does.
4. **Generate 3D from Picture** turns a photo or sketch into a mesh with the meshgen service
   (8902). It uses the **Picture** field in this box, or — when that is empty — whatever is
   attached in the **Assistant** box above, so a picture dragged in up there does not have
   to be found twice; the panel says which one it will send. It takes about **five
   minutes** on this machine and the button says so before it is pressed. While it runs, the
   box shows the stage meshgen is actually on (`RemeshMesh (2:05) ...`) rather than a
   spinner, with the caveat that the bar is progress through *that stage*, not the job.
   What lands is voxel-repaired (see `import_generated` above) and the status line names the
   object, the time it took and the face count, then points at **Check imported model**.
5. **Merge for Print** fuses everything **selected** into one sealed shell (`merge_for_print`
   above), at a voxel size taken from the printer's nozzle — no field to fill in, because
   the honest default is a machine number. The hint under the button counts what is
   selected, the button is greyed until something is, and the status line afterwards names
   the shell, the voxel size and the next press (**Check imported model**). The originals
   are hidden, not deleted, and Ctrl+Z takes the merge back.
6. **Voxel Repair** rebuilds the surface as one closed shell at the given detail size. A
   mesh with holes cannot be sewn into a solid, so the service refuses it and says so; that
   refusal is passed through word for word, the box turns red, and the fix is this button.
   The repair goes through the ordinary `remesh` command, so it gets its own named undo
   checkpoint like everything else. Detail finer than the voxel size is lost — the panel and
   the assistant both say so.

Two things a raw mesh cannot have: `solid_is_valid` is null (there is no B-Rep to validate,
so `watertight` is the triangles' own closedness), and a mesh above 500 000 faces is refused
up front with the fix attached rather than being streamed at the service for a minute.

The same operations are socket commands (`check_model`, `segment_model`, `import_generated`,
`merge_for_print` above) and MCP tools (`check_model`, `segment_model`, `generate_3d`,
`merge_for_print` — `mcp/README.md`), so the assistant can do all of this from the chat box.
The saved flow **merge-and-check** (`flows/merge-and-check.json`) is the merge and the check
as one press.

## Flows box

`View3D ▸ N sidebar ▸ Forge ▸ Flows` — directly under PartForge, because that is where a
flow's steps usually land.

A **flow** is a job that was worked out once and written down: a named sequence of Forge
operations with parameters, stored as JSON in the repo's `flows/` folder, that replays
exactly the same way every time **with no AI involved**. The assistant is worth a turn the
first time; the tenth time it should be a button.

- The refresh button re-reads the folder. Each flow is a row: click its name to select it
  (the description appears underneath), press **Run** to replay it.
- The selected flow's parameters are editable fields right there — the wedge count, the
  joint tolerance, the target collection — filled in with the flow's declared defaults.
- The result goes into the status line (`segment-into-4: 2 step(s) in 4.3s`), with the step
  labels underneath. A failure names the step that broke, not just the flow.
- A file that will not parse is **listed with its error** rather than quietly vanishing.

### Editing a flow

The pencil toggle in the Flows header turns the box into a modest editor for the selected
flow:

- its **description**, in your own words;
- its **default values** — the same fields, saved back into the file instead of used for
  one run;
- its **steps**, listed by label with up/down arrows and an X.

Nothing is written until **Save**: reordering and deleting happen on a working copy held in
the panel (the header says *unsaved*), and the loop-back button next to Save throws the
edits away and re-reads the file. Save writes the JSON back with each default typed like the
one it replaced — a `wedges` of `4` stays a number when you type `6`, not the text `"6"` —
revalidates the whole document, and **refuses to save fewer than two steps**, which is the
same rule `flow_save` enforces for the assistant: a one-step flow is a button for something
that is already a button, and it hides what it does.

Steps cannot be *created* here, and that is deliberate. Writing a step means knowing a
command name and its arguments; that is the assistant's job (it offers to save repeatable
work as a flow) or the JSON's. A half-built step editor would only be a worse way to reach
the same file.

The folder is an add-on preference: `Edit ▸ Preferences ▸ Add-ons ▸ Forge ▸ Flows ▸ Flows
Folder` (`forge_flows_dir`), defaulting to the repo's `flows/` directory, derived from the
add-on's own location the same way the printer profile is. Installed from a zip there is no
repo above the add-on, so it comes back empty and the box says so rather than guessing.

Async like everything else here: the run happens on a worker thread so a 300-second
`/segment` does not freeze Blender, and the Blender steps are marshalled back to the main
thread through the operator's own poll timer. The same flow run over the socket
(`flow_run`) is already on the main thread and skips all of that.

The JSON format, the `{{param}}` and `{{steps.0.result.field}}` placeholders and the
`script_path`/`printer_path` conveniences are documented in **`flows/README.md`**. The
matching MCP tools are `flow_list`, `flow_run` and `flow_save` (`mcp/README.md`);
`flow_save` is the only thing that writes into `flows/`.

## RigForge panel

`View3D ▸ N sidebar ▸ Forge ▸ RigForge`. Unlike PartForge, **nothing here needs the
geometry service** — RigForge is pure Blender.

### The tagging model

Everything downstream depends on Blender knowing what the parts of your sculpt are, so
that is the only manual step:

- A **tag** is a vertex group named `tag_<Name>` — `tag_Head`, `tag_Arm.L`, `tag_Ear.R`,
  `tag_Tail`. The commands and the panel take the bare name (`Head`); the `tag_` prefix
  is added for you, and accepted if you type it. Nothing else on the object is touched,
  so deform weights and sculpt masks live alongside tags without colliding.
- A vertex is in a tag at **weight 1.0** — tags are memberships, not gradients.
- A **face** belongs to a tag when *every* one of its vertices does. That is the rule
  both writing and reading use, so tagging faces and reading faces back round-trips
  exactly. A face straddling a boundary belongs to neither side, which is what leaves a
  one-face band between regions for the seam to run along.
- Face indices are **base-mesh polygon indices** (`object.data.polygons`), not evaluated
  or modifier-applied ones.
- The `character.json` **manifest** is the same information in a form that outlives the
  .blend: the tag list, the archetype, and your motion notes. Vertex groups are the truth
  for *where* a tag is; the manifest is the truth for *what the character is*.

Selection-based tagging works in the face-select mode you will actually be in: the
handler reads the live edit-mode bmesh (the base mesh's `polygon.select` flags only catch
up when Blender leaves Edit Mode), assigns in Object Mode because that is the only place
`vertex_group.add()` is legal, and puts you back in Edit Mode where it found you.

`rigforge_untag` with a face list is conservative by default: it only drops vertices that
no *remaining* face of that tag still needs, so a shared border between two patches
survives and the neighbours keep their tag. Untagging a scattered handful of faces whose
every vertex is shared is therefore a legitimate no-op. Pass `include_shared: true` for
the blunter Blender-style behaviour (remove every vertex of those faces, eroding the tag
by one ring around the hole).

### Panel walkthrough

1. **Tags.** One row per tag: name, face count, vertex count, and three buttons —
   *select* (enters Edit Mode with that tag's geometry selected), *assign from selection*,
   and *remove*. Type a name in the field at the bottom and press **+**: with faces
   selected it tags them straight away, with nothing selected it creates the tag empty so
   you can fill it later.
2. **Character.** The character name written into the manifest, the **archetype**
   (biped / quadruped / custom — this picks the rig template in Phase 4), and
   **motion notes**, in plain language: *"ears are floppy and lag behind the head"*,
   *"tail drags on the ground"*, *"hops rather than walks"*. Stage 4 reads those notes to
   set how much each chain lags (see [secondary motion](#the-rig-phase-4-stage-4)). All
   three live as custom properties on the object, so they
   follow the sculpt rather than the scene; the ⟳ button next to the object name reloads
   the panel from whatever object is active.
3. **Manifest.** A path plus **Save** / **Load**. Save writes `character.json` (tags come
   from the vertex groups, everything else from the object). Load applies the archetype
   and notes and creates an empty vertex group for any tag the file lists that the mesh
   does not have yet — so a manifest can seed the tag list before you have tagged
   anything. Keys the add-on does not understand yet (`actions`, `godot`, anything Phase 4
   and 5 add) are carried through a load/save round trip untouched.
4. **Retopo**, **UV**, **Rig**, **Cloth**, **Actions** and **Godot Export** — below, in the
   order you run them.

Every button reports into the panel's own status line, in the same style as PartForge: a
failure shows up in the sidebar, never as a traceback in a console you are not looking at.

### The retopo pipeline

**Retopo ▸ Retopologise** (`rigforge_retopo`) turns the sculpt into a game mesh. The
sculpt is **never modified and never deleted** — `keep_original` is always true in v1, and
asking for false comes back as a warning rather than a refusal. Re-running replaces the
previous `_retopo` / `_lod*` objects in place, so the names stay stable.

1. **Duplicate.** `<obj>_retopo`, in the same collection(s), with the sculpt's modifiers
   and vertex groups stripped off the copy.
2. **Voxel remesh** at an adaptive size. This step is the whole reason the pipeline is
   robust against a real sculpt: overlapping blobs, self-intersections and holes are all
   non-manifold, and Quadriflow refuses non-manifold input outright. The voxel size is
   derived rather than fixed — a surface of area *A* remeshed at size *v* lands around
   `A / v²` quads, so the size is solved backwards from four times the target face count
   and then clamped to between 1/400 and 1/16 of the object's longest axis. The same call
   therefore works on a 3 cm trinket and a 3 m creature.
3. **Quadriflow** to `target_faces`. Presets: **desktop 15000**, **mobile 5000**; an
   explicit `target_faces` (or the panel's field, when it is above zero) wins, and a value
   stored in the manifest's `retopo` block comes next. If Quadriflow still fails, the run
   does not die: it falls back to a collapse decimate to the same target and says so in
   `warnings` and in `quad_method`.
4. **Shrinkwrap** the result back onto the sculpt (`NEAREST_SURFACEPOINT`, applied), so
   the quads sit on the original silhouette rather than on the voxel approximation of it.
   No modifiers are left on the output.
5. **Tag transfer by proximity.** A KD-tree of the sculpt's vertices, and each retopo
   vertex takes a majority vote over its three nearest neighbours — deterministic, needs
   no modifier stack or depsgraph evaluation, and one stray vertex on a border cannot
   claim a region. Every `tag_*` group is recreated on the retopo mesh.
6. **UV unwrap** (`unwrap`, default **on**) of LOD0, through the same code the UV panel
   runs (see *Auto UV* below), controlled by the same `seams_from_tags` / `margin` /
   `angle_limit` parameters.
7. **Normal bake** (optional, `bake_normals`). High-to-low, Cycles, into a new image named
   `<obj>_retopo_normal` at `bake_resolution` (default 2048); `bake_path` also writes it
   out as a PNG.
8. **LODs** (optional, `lods`). `<obj>_lod1`, `_lod2`, … simplified **from the unwrapped
   LOD0** to a triangle budget, so the tags *and the atlas* come with them. By meshoptimizer
   when `native/meshopt/meshoptimizer.dll` is built, by Blender's Decimate when it is not —
   named either way in `simplifier`.

Returns the object names, a `face_counts` map keyed by name, the per-stage log, the
transferred tags, the UV report, the bake report, `lod_reports`, `simplifier`, `budget` and
any warnings.

**The order is the feature.** The canonical finishing chain is
`repair → retopo → UV unwrap → bake high→low → tangents → LOD`, and three of those
orderings are not negotiable:

- **Unwrap before bake.** A bake needs somewhere to write. If nothing has unwrapped the
  mesh, the obvious somewhere is an empty default UV layer — and the real unwrap that
  follows replaces every coordinate in it, so the map decodes against a layout that no
  longer exists. Nothing errors; the asset just looks subtly wrong. So `bake_normals` with
  `unwrap: false` is **refused with a sentence**, and `bake_normals()` itself refuses any
  mesh that carries no record of a real unwrap (the `forge_uv_unwrapped` custom property,
  written only by `rigforge_auto_uv` / the stage above).
- **LODs from the unwrapped LOD0.** Every level is duplicated from LOD0 — never from the
  level above, which would compound UV drift — so one baked map serves the whole chain.
- **Tangents on export**, not regenerated by the engine. See *Godot export* below.

**LOD budgets, not ratios.** `0.5 ** level` says nothing about what an asset may cost:
half of a 200 000-triangle mesh is still unshippable. Levels are cut to **triangle
budgets** instead:

- **Game budgets**: desktop **50 000**, mobile **10 000** triangles. `result.budget` states
  the platform budget, LOD0's achieved triangle and face counts and a `within_budget` flag,
  and going over is a warning.
- **The default chain** starts at LOD0's own triangle count (capped by the game budget)
  and takes a quarter per level, floored at 64. Override the whole chain with
  `lod_budgets: [2400, 600, 150]`.
- **Each level's achievement is asserted** in `lod_reports`: `budget`, `ratio`,
  `triangles`, `face_count`, `within_budget`, plus the **measured** geometric error
  (`error`, `error_mean`, `error_relative` — max/mean distance from LOD0's vertices to the
  level's surface, by `closest_point_on_mesh`) and the `visibility_begin` distance that
  error implies. A budget it could not reach is a warning, not a silent miss.
- **`visibility_begin`** comes from a screen-error model: an error *e* seen from distance
  *d* subtends `e / d` radians, which is `e / d × 704` pixels at 1080p and a 75° vertical
  FOV, so the level is honest from `e × 704 / 1 px` onward. Those are the numbers the Godot
  export turns into `visibility_range_begin` / `_end`.

**Which simplifier cut the chain — `lod_reports[].simplifier`.** Two are wired up, and the
report says which ran, at the top of the result and on every level (and on each LOD object's
`forge_lod` property, so the exporter can see it too):

- **`"meshopt <commit>"`** — meshoptimizer's `simplifyWithAttributes` through
  `forge.tools.meshopt`, used whenever `native/meshopt/meshoptimizer.dll` is built. Its
  result is an index buffer into the *original* vertex buffer, so a surviving vertex keeps
  its original position **and its original UV, bit for bit**: the levels do not merely
  sample LOD0's atlas closely, they carry it. The report gains `result_error`,
  `achieved_index_count`, `target_index_count` and the `attribute_weights` used.
- **`"blender-decimate (meshopt unavailable: <reason>)"`** — the Decimate fallback, with the
  reason the DLL was not there quoted in full. Correct, and UV-blind; see the limitation
  below. A meshopt run that fails mid-flight falls back the same way, as
  `"blender-decimate (meshopt failed: ...)"`.

**Known limitation — UV distortion under decimation (the fallback path).** Blender's
Decimate **Collapse** is a position-only quadric with no UV term at all (the modifier's
`delimit` option belongs to Planar/Dissolve), so UV distortion across a collapse is not
bounded by anything. Measured on the synthetic character in `headless_rigforge.py`, sharing
LOD0's atlas holds well in practice — median UV drift 0.0004 at LOD1 and 0.0011 at LOD2,
90th percentile under 0.007 — but the worst case is a torn island (0.63 at LOD1 on the same
run), not a small stretch. The one lever Collapse exposes is its vertex group, and measured
on Blender 5.0.1 that group is a **hard lock, not a soft cost**: a vertex in it is never
collapsed, whatever its weight (1.0, 0.5, 0.05) and whatever `vertex_group_factor` says
(0.5 through 100 behave identically). On an unwrapped character the seam vertices *are* most
of the budget, so locking them floors the reduction far above any LOD target. `protect_seams`
(default **off**) turns it on for the cases where an exact atlas matters more than the face
count, and the budget miss is reported.

**`protect_seams` is unnecessary under meshopt**, and ignored there. A UV seam is an
attribute discontinuity the quadric already prices, the seam vertices are not locked, and
the survivors keep their exact UVs — so there is nothing to protect and none of the budget
is spent protecting it. The flag stays for the Decimate fallback only.

### meshoptimizer (`native/meshopt`, `forge.tools.meshopt`)

A ctypes binding over meshoptimizer's C interface, built from vendored MIT sources pinned at
`3d62f11a` (2026-09-12; the floor is 2026-09-09's "Stabilize many experimental APIs", and
the `v1.2` tag predates it). Build it with `native\meshopt\build.ps1`, which probes for
`cl.exe`, `clang++`, `cmake`, then `zig c++` and compiles the whole library in one command —
it is dependency-free C++ with no configure step. `native/meshopt/README.md` has the
install-one-of-these table; **no C++ toolchain is present on this machine**, so the add-on
currently runs the Decimate fallback and says so in every report.

`simplify_lod(vertices, indices, uvs, normals, target_index_count, target_error, weights)`
returns `(indices, result_error)`. It takes flat sequences, imports no `bpy` and uses no
numpy (`array` buffers pointed at by ctypes, because `foreach_get` hands back flat lists),
so it is testable and reusable outside RigForge. Three traps it encodes, all of them silent
in the raw API:

- **unwelded input no-ops.** The simplifier rebuilds topology by hashing the *raw float bits*
  of each position, so UV-split vertices stitch back together into wedges — but a triangle
  *soup*, where every corner carries its own position, has no shared edges at all and
  collapses nothing while reporting success. `weld_report` counts both cases and
  `simplify_lod` refuses a soup by name, before it even loads the library.
- **`result_error` is not a success signal**: it is the error of what the simplifier *did*,
  and a run that collapsed nothing reports a beautiful 0.0. The achieved index count is
  always measured, and `require_target=True` turns a miss into `MeshoptTargetMissed`.
- **gltfpack's UV weight is 0.0.** Its `simplifyAttributes` weights normals 0.5, colours 1.0
  and texture coordinates `update ? 1.f : 0.f` — copying that call site verbatim is the
  documented way to build a UV-blind "UV-aware" simplifier. The weight here is derived from
  the mesh's own reciprocal UV density (world units per UV unit × a priority factor, clamped
  to the researched 10–100 per-scalar band), so one number works for a 2 m character and a
  20 cm prop. Flags otherwise follow gltfpack: `Permissive` + `Prune`.

Missing DLL is never an error: every entry point still imports, `unavailable_reason()`
returns one sentence (read from `native/meshopt/meshopt_build.json`, which the build script
writes on success *and* on failure), and `addon/tests/headless_meshopt.py` stays green —
library-dependent checks skip with that same reason, while argument validation, the weld
census, the UV-weight derivation and the **binding arities checked against the vendored
`meshoptimizer.h`** all still run.

**Bake caveat.** The bake is the one step allowed to fail. It needs Cycles, and Cycles is
a shipped add-on that a `--factory-startup` session (or a user who turned it off) leaves
disabled — so the add-on enables it for you and then lets `scene.render.engine = "CYCLES"`
be the real test, because `engine` is a dynamic enum whose static RNA item list does not
list registered engines. If any of it fails, `result.baked` comes back as
`{"ok": false, "reason": "..."}`, the reason is repeated in `warnings`, and **the rest of
the pipeline still delivers its meshes** — everything else is already on disk by then.
Cycles bakes on the CPU here at one sample; a 2048 map on a dense sculpt is not instant,
so start smaller if you are iterating. Baking needs a **real** unwrap on the low-poly mesh
and refuses without one (see *The order is the feature* above) — it no longer invents a UV
layer to write into, because that layer is exactly what the next unwrap throws away.

### Auto UV

**UV ▸ Unwrap** (`rigforge_auto_uv`) derives seams from the tags, unwraps and packs.

- **Seams from tags** (the default): every edge where the tag changes becomes a seam.
  That is exactly the neck, shoulder and wrist seams the plan asks for, and it falls out
  of the tagging rule rather than being special-cased. Open boundaries are always seams —
  a hole cannot be unwrapped through.
- **Fallback.** A mesh with fewer than two distinct tagged regions has no boundaries to
  use, so it seams by sharp angle instead (`angle_limit`, default 66°, the same threshold
  Smart UV Project uses).
- **Unwrap** is angle-based along those seams, with `uv.smart_project` as a second
  fallback if Blender refuses; then **pack** with the concave packer, rotation and scaling
  enabled, at `margin` (default 0.02).

Returns the island count, the UV coverage as a fraction of the 0–1 tile, the seam counts
broken down by where they came from, and which unwrap method actually ran. For an organic
shrinkwrapped quad mesh, coverage lands around **0.50–0.55** — that is what Blender's
packer gives for concave islands, not a bug. UV selection is driven with
`use_uv_select_sync` turned on for the duration, because there is no UV editor to select
in when this runs headless; your setting is restored afterwards.

### The rig (Phase 4, stage 4)

`RigForge ▸ Rig`. Three buttons, a weight row and a gate, in the order you use them.
(The fourth button, **Check Deformation**, is
[the deformation harness](#the-deformation-harness-rig_check); it is drawn disabled until
something is skinned to a rig, because there is nothing to measure before that.)

**Nothing is downloaded.** Rigify is not a download — it ships inside Blender
(`scripts/addons_core/rigify`) and is simply switched off in a `--factory-startup`
session, so the add-on enables it in-session the same way the normal bake enables Cycles.
One wrinkle worth knowing if you write similar code: Rigify must be enabled with
`default_set=True`, because its own `register()` reads
`preferences.addons["rigify"].preferences` and raises `KeyError` if the add-on was not
written into the preferences first.

**Place Metarig** (`rigforge_metarig`) does not merely scale a template onto the sculpt.

1. **Measure.** Every tag becomes a *region*: its bounds, its centroid, its dominant axis
   (power iteration on the point cloud's covariance — no numpy, the add-on stays stdlib +
   `bpy`), and landmarks along that axis. Landmarks average a slab of vertices rather than
   picking the extreme one, so a stray vertex cannot move a joint. A region that is
   nearly round — a blob arm, a ball head — has no meaningful principal axis, so it falls
   back to the anatomical hint instead (arms measure outward from the torso, legs
   downward).
2. **Scale and seat.** The metarig is uniformly scaled to the sculpt's height and sat on
   its floor, and the transform is applied into the armature. Everything the fitter does
   *not* touch — feet, fingers, the whole face — rides on this step.
3. **Fit.** Then the landmark bones are snapped: hips → chest along the spine (each
   vertebra at the centroid of its own slice, so the spine follows a belly), neck and
   head from the Head tag's bottom and top, shoulder/elbow/wrist from each Arm tag,
   hip/knee/ankle from each Leg tag. Bones are placed parent-first, and any child that is
   *not* itself being fitted is dragged along by the same delta its parent's tail moved —
   which is what keeps a hand (and its fingers) on the end of a re-fitted forearm and the
   whole face on top of a re-fitted head.
4. **Two details that are not cosmetic.** A perfectly straight limb — which is exactly
   what a blob sculpt measures as — leaves Rigify's IK pole undefined, so a minimum bend
   is inserted at the elbow (backwards) and knee (forwards). And Rigify refuses to
   generate when one chain's head does not sit exactly on the tail of the chain above it
   (*"bone position is disjoint"*), so the spine and the neck are fitted from a single
   shared junction point rather than each computing its own.

**Missing tags are warnings, never errors.** A sculpt tagged only Head and Torso still
gets a rig; the untouched bones keep their scaled default and the result says so.

**Template choice.** `archetype` comes from the manifest by default (`auto`). Within
biped, the **preset** decides which Rigify human you get:

- `auto` (default) → the **29-bone basic human**, unless the sculpt carries face or hand
  tags (`Face`, `Jaw`, `Eye`, `Hand`, `Finger`, …), which earn the full one. A game
  character does not want 160 deform bones for a face nobody tagged.
- `human` → `bpy.ops.object.armature_human_metarig_add`, the full template (face,
  fingers, ~160 deform bones).
- `basic_human`, `quadruped` (and Rigify's `wolf`/`cat`/`horse`/`bird`/`shark` samples).

The result reports `preset` and `preset_reason` every time, so the choice is never
silent.

**Chains, and secondary motion v1.** A tag named like a chain (`Ear.L`, `Tail`,
`Antenna`, `Whisker`, `Fin`, `Tentacle`, `Horn`, …), or listed in `modules` as
`{"kind": "chain", "tag": "..."}`, or named in `spring_chains`, becomes a straight
three-bone chain along its own axis, anchored to whichever existing bone is nearest its
base and flagged `basic.copy_chain` so Rigify generates controls and `DEF-` bones for it.

Blender has **no spring-bone primitive**, and every real one is a download. What Generate
Rig builds instead, per chain:

- `MCH-lag-<tag>` — a bone at the chain's tip, parented to the chain's parent. It follows
  the head exactly.
- `MCH-lagmix-<tag>` — the same place, but parented to `root`, with a Copy Transforms
  constraint onto `MCH-lag-<tag>` at influence `follow`. At influence 1 it tracks the head
  perfectly; below 1 it blends between the head's motion and its own rest pose, so it
  arrives **late and short**.
- every `DEF-` bone of the chain gets a Damped Track onto that mixer, influence rising
  towards the tip.

So the ears swing towards where the head *was*. It is a pure function of the pose — no
state, no frame order, no simulation cache — which means it bakes into an exported action
like any other constraint. It approximates inertia; it does not simulate it. Hence
**secondary-motion v1**. `follow` is read out of your motion notes: *floppy / lags /
trails / drags / loose / heavy* → 0.45, *bouncy / jiggle / springy* → 0.60,
*stiff / rigid* → 0.88, otherwise 0.70. Both helper bones are non-deform, so they never
reach Godot.

**Generate Rig** (`rigforge_generate_rig`) runs Rigify's own generate (capturing its
errors, including the bone it names, into a readable message), then:

- **Automatic weights.** `parent_set(type='ARMATURE_AUTO')`. Bone-heat weighting flatly
  refuses some meshes, and — worse — sometimes succeeds while leaving vertices with no
  weight at all. Both cases are detected: a hard failure falls back to distance weights
  (inverse-square distance to the bone *segment*, top 4, normalised), a partial one is
  repaired vertex by vertex. Either way the result's `weights.method` says which ran, and
  it is a warning, not a silent difference.
- **Per-tag cleanup.** The plan's rule, stated exactly, is *no head weights below the neck
  tag*. Generalised: a vertex inside tag **T** may only be weighted to a bone belonging to
  another tag **O** when the vertex is within a tolerance **band** of O's own region
  (default 6% of the character's longest dimension). That band is what keeps the shoulder
  and neck blending instead of shearing. Bones are matched to tags from the metarig's
  recorded mapping first (`DEF-upper_arm.L` *and* `DEF-upper_arm.L.001` — Rigify
  subdivides), then geometrically for anything left over, which is also the whole answer
  for a rig this add-on did not build.
- **Limit and normalise.** Top 4 influences per vertex, summing to 1. Done in Python
  rather than through `vertex_group_limit_total`, because tags live in the same
  vertex-group namespace as deform weights and the operator's group filters are not a safe
  way to protect them.
- Anything the cleanup strips bare is re-filled from the nearest bones rather than left
  unweighted, with a warning.

`cleanup_report` carries the numbers per tag: vertices in the tag, weights zeroed, total
weight removed, and which bones were the offenders. On the test biped (5 082 faces, eight
tags) it zeroes about 4 700 weights, most of them ear bones reaching into the head and
head bones reaching down the torso.

**Weights ▸ Report / Cleanup / Normalize** (`rigforge_weights`) is the same machinery on
its own: `report` counts per-bone influences and finds unweighted, un-normalised and
over-influenced vertices; `cleanup` re-runs the per-tag rules; `normalize` only limits and
normalises.

### The rigging bridge (Phase 4, stage 4a): `joints_file`

`rigforge_metarig` has two sources of truth about where a joint is — the **tags** the
artist painted and the **template** Rigify hands us. `joints_file` adds a third: a neural
joint detector's predictions, read from a JSON file.

**Why a file, and why hints.** The detector runs outside Blender, in its own Python and
its own CUDA (`rigbridge/detect_joints.py`, driving UniRig; see `rigbridge/README.md` and
`C:\forge-models\unirig\FORGE-NOTES.md`). It is a **separate process with a file
handoff**, not a service: it wants ~8.5 GB of a 12 GB card, and holding that resident
would starve the mesh generator. The file boundary also means the blending rules below are
testable to the millimetre with no model, no CUDA and no download — which is exactly how
they are tested.

And the predictions are *hints*, not answers, because the evidence says so
(`docs/automation-thesis.md` build #2): these are joint **detectors**, not riggers —
F1 ≈ 0.077 on out-of-domain skeletons, no hands, no tails, no wings, and on anything
outside a template mode **no names at all**. Measured here, on our own test biped: UniRig
put the spine within 8–36 mm and the shoulders 253–266 mm out. A source like that is worth
listening to and must never be obeyed.

So the blend is deliberately asymmetric:

- **Tags anchor.** Every landmark is still computed from the tagged geometry first.
- **Predictions refine.** A prediction within `joints_tolerance` (default 12% of the
  mesh's largest dimension) of that landmark pulls it `joints_weight` (default **0.5**) of
  the way in. Half: a detector that is right pulls the joint into the flesh, one that is
  slightly wrong costs half its error rather than all of it. `joints_weight: 0` reports
  everything and moves nothing; `1` hands the landmark over outright.
- **Tags win, loudly.** A prediction past tolerance but inside `joints_disagree_band`
  (default 3×) is recorded as a **disagreement** — with both positions in millimetres —
  and ignored. The fit is unchanged and the report says a second opinion existed and was
  overruled.
- **Best effort past the wrist.** Roles no tag can express (fingers, toes) are placed
  from *named* predictions alone, flagged `best_effort`, never counted as fitted. A
  connected bone whose parent the tags fitted is skipped instead, with the reason.

**Matching, when the detector gives no names.** Two matchers run per role: by **name**
(`hips`/`pelvis`, `spine`/`chest`, `upper_arm`, `forearm`/`elbow`, `hand`/`wrist`,
`thigh`, `shin`/`calf`, `foot`/`ankle`, with a side that must agree), then by **position**
— the nearest unclaimed prediction. Position is the path that actually runs: UniRig's
joints arrive named `bone_0`, `bone_1`, …, and those are read as **unnamed**, because a
placeholder that looks like a name is worse than no name at all (the positional matcher
deliberately refuses to touch a joint whose name says it belongs to another role).

Two rules keep positional matching from inventing anatomy:

1. A prediction is **claimed** once used, so two adjacent spine landmarks cannot collapse
   onto the same predicted vertebra.
2. A positional match must also fall inside the prediction's **own territory** — closer to
   this landmark than half the way to the next predicted joint. Without that, a landmark
   the detector simply did not predict (a knee it missed) reaches over and grabs its
   neighbour's ankle. This is why the effective tolerance in the report is often far below
   the nominal one (43 mm at the shoulder of a 1.7 m figure, not 205 mm).

**Disagreements are decided at the end, not on the spot.** A landmark with no prediction
of its own always has *some* neighbour's joint as its nearest; calling that a disagreement
would bury the real ones. A near-miss becomes a disagreement only if no other role claimed
that prediction (or if the prediction's *name* said it was this role's); otherwise it is
recorded as `explained_elsewhere`.

**The frame gate.** The file declares its own frame (`unit`, `space`, `axis_up`) and it is
converted — millimetres to metres, glTF Y-up to Blender Z-up when asked, then through
`matrix_world`. If fewer than half the converted joints land inside the mesh's (10%-grown)
bounding box the **whole file is refused**, the fit falls back to tags alone, and the
warning says which unit/axis reading *would* have worked. A frame mistake that silently
shifted every joint is the one failure that looks exactly like success.

The result's `joints` block carries all of it: `enabled`, `joints`, `named_joints`,
`placeholder_names`, `inside_fraction`, the effective tolerances, `refined` (with
`tag_mm`, `predicted_mm`, `used_mm`, `moved_mm` per role), `disagreements`,
`best_effort`, `considered` and `unused_joints`. Two of the warnings are written for
someone who will never open it: one counts the disagreements and names the worst, one
counts the refinements and gives the largest move.

```jsonc
// what the runner writes; what this command reads
{"schema": "forge.joints/1", "source": "unirig",
 "frame": {"unit": "mm", "space": "mesh_local", "axis_up": "Z"},
 "joints": [{"index": 0, "name": null, "head_mm": [3.3, -10.0, 634.2],
             "tail_mm": null, "parent": null, "confidence": null}]}
```

Nothing changes without `joints_file`: the tag-only fit is bit-for-bit what it was.
Quadruped metarigs read the file and decline to use it (their front/rear limb roles have
no agreed names yet) and say so in a warning.

### The deformation harness (`rig_check`)

`RigForge ▸ Check Deformation` (`rig_check`). Every rigging tool stops at "the rig
generated". This answers the question the artist asks next — **does it deform** — with
numbers instead of a screenshot.

For each deform-relevant joint it poses the rig to extremes and measures three things on
the **evaluated** (armature-modified) mesh:

1. **Volume loss** — the collapsed elbow. The convex hull of the vertices around the
   joint, posed against rest. A hull because the surface around a joint is an open patch
   whose signed volume means nothing, while the hull of the same vertices is closed,
   cheap, and collapses exactly when the flesh does. A negative loss is a joint whose
   neighbourhood *grew*, which is information, not an error.
2. **New self-intersections** — the arm through the ribs. The same BVH overlap
   `mesh_diagnose` uses, differenced against the rest pose so a mesh that already clips
   itself does not fail every joint on the rig.
3. **Candy-wrapper twist** — the forearm pinched to a thread. The area of the 2D convex
   hull of the vertices in a thin slab across the bone, 30% of the way along it, posed
   against rest.

**The neighbourhood scales with the limb, not just the bone.** A radius measured only in
bone lengths sits entirely inside a blob leg and catches no surface at all — which is how
a joint gets silently skipped. The radius is the larger of 60% of the shorter adjacent
bone and 1.25× the limb's measured girth (the median distance from its own vertices to the
bone). Both numbers are in the report (`neighbourhood_radius_mm`, `limb_girth_mm`).

**Three poses per joint** (`extreme`, the default): mid-flex, max-flex, max-twist. A sweep
would be hours; the extremes are where the failures live and the mid-flex catches a joint
that is already wrong before it gets anywhere. `quick` is one pose, `full` is seven, and
`poses` also takes an explicit list (`[90, 140]` or
`[{"label": "...", "flex_deg": 90, "twist_deg": 30}]`). Ranges are per joint and
conservative — knee 0–140°, elbow 0–150°, hip 0–110°, shoulder 0–95°, spine and neck
bends — a game character reaches those long before a gymnast's.

**Three things stop it from passing a rig that did nothing:**

- It poses the **control**, not the deform bone (`upper_arm_fk.L`, `chest`, `neck`),
  preferring *unconstrained* bones — a Rigify `ORG-`/`DEF-` bone is driven by constraints
  and rotating it changes nothing while looking exactly like a pass.
- It forces every `IK_FK` property to full FK for the duration, because an FK rotation
  under an IK solver moves nothing either.
- It then **proves** the joint drove geometry: if the poses move no vertex further than
  0.2% of the mesh's span, the joint is reported unmeasured with that reason rather than
  scored.

**The pose is always restored.** Every pose bone's `matrix_basis`, rotation mode and
`IK_FK` value is captured up front and put back in a `finally` — finished, raised or
interrupted. The suite asserts this bone by bone.

**The thresholds are heuristics and say so.** ≤8% volume loss / ≤20% is attention, 0 new
intersections / ≤20 is attention, ≤15% twist collapse / ≤35% is attention. They are
**proxy tier**: the points at which each artefact becomes visible in practice, *not*
calibrated against artist accept/reject decisions — calibrating them against real
accepted and rejected rigs is the obvious next round. Every measurement is printed next to
the band that judged it, so you can disagree with a threshold and keep the number.

A real report, on the synthetic test biped (a blob with automatic weights — it is supposed
to fail):

```
gate=fail   11 joints measured   0.67 s
  knee.L       vol=41.7%  twist=-0.7%  new clips=108   fail
  elbow.R      vol=52.7%  twist= 5.5%  new clips= 96   fail
  shoulder.L   vol=22.9%  twist=-6.9%  new clips=106   fail
  spine_lower  vol= 3.7%  twist= 1.5%  new clips=  4   attention
  neck         vol= 2.6%  twist= 8.1%  new clips= 12   attention
```

Runs under `--background` in well under a second on a 5 000-face mesh, renders nothing,
downloads nothing and writes nothing to disk.

**What the MCP mirror will need** (the follow-up round, in `mcp/`, not touched here):

- `rig_check` as a tool, with `rig`, `mesh`, `poses`, `joints`, `max_poses`,
  `intersections` — and a **summariser**, because the full result is one object per joint
  per pose and the agent only needs `gate`, the failing joints, and their worst three
  numbers. Feed the LLM the computed features, never the raw report.
- `rigforge_metarig`'s five new parameters, `joints_file` first; the MCP layer should
  resolve a relative path against the project workspace the way the other file-taking
  tools do.
- A wrapper that *runs* the detector — the runner is a subprocess in a foreign venv
  (`C:\forge-models\unirig\.venv\Scripts\python.exe rigbridge\detect_joints.py`), so the
  tool is "export mesh → run runner → hand the JSON path to `rigforge_metarig`", and it
  must refuse to start while a meshgen job holds the GPU (port 8902).
- The honest caveats in the tool descriptions: predictions are hints, disagreements mean
  the tags won, and `rig_check`'s thresholds are heuristic — otherwise an agent will read
  a `fail` as a fact about the artist's work rather than a band it can argue with.

### Godot export (Phase 4, stage 7)

`RigForge ▸ Godot Export` (`rigforge_export_godot`). Path, all-or-named actions, LODs,
root motion, Export.

**Why there is no Game Rig Tools here.** The plan named that add-on for the
control-rig → deform-rig conversion. It is a download, so the conversion is implemented
natively, and it is four honest steps:

1. Duplicate the generated rig into a temp collection, and strip its constraints **before**
   deleting anything (a constraint whose subtarget has just been removed makes the
   depsgraph complain on every evaluation for the rest of the export).
2. Work out where each `DEF-` bone's parent *went*: Rigify parents deform bones to
   `ORG-`/`MCH-` bones, so walking the original hierarchy and mapping each ancestor back
   to its `DEF-` counterpart reconstructs the anatomical parenting the metarig described.
   Orphans land on `root`.
3. Delete every bone that is not a deform bone (plus `root`), and re-apply that parenting.
4. Constrain each deform bone to its namesake on the control rig with Copy Transforms and
   bake with `nla.bake(visual_keying=True)`, one action at a time. Whatever the control
   rig's IK, drivers and secondary motion resolve to on a frame is what lands on the
   deform bone — so the exported clip needs none of that machinery.

Then the glTF, with Godot's conventions:

- **`use_selection`, `export_yup=True`** — glTF is a +Y-up format and the exporter does the
  conversion; unit scale is 1.0 = metres (override with `unit_scale`).
- **Applied transforms.** Rotation and scale are applied on the export copies, so a
  character that sat rotated in the .blend does not arrive rotated in Godot.
- **`export_animation_mode="NLA_TRACKS"`.** Each baked clip is pushed onto its own NLA
  track named after the action. This matters: in `ACTIONS` mode the exporter also picks up
  actions belonging to *other* objects in the file, and a two-action character ships with
  five animations. With NLA tracks the file contains exactly what was baked.
- **Action names survive.** Blender will not hand out a name the source action still
  holds, so the source steps aside for the duration of the export and is put back
  afterwards; the clip reaches Godot as `idle-loop`, not `idle-loop.001`. The temporary
  baked actions are deleted on the way out.
- **Tangents are exported.** Khronos' glTF exporter defaults `export_tangents` to
  **False**, so an asset that does not ask for them ships tangent-less and every engine
  regenerates the basis with its own algorithm — which is not the basis the normal map was
  baked against. The map then decodes slightly wrong, everywhere, and nothing errors. The
  export sets `export_tangents=True` alongside `export_normals=True` (the exporter ANDs
  the two internally, so normals are asked for rather than relied on),
  `export_texcoords=True`, `export_materials="EXPORT"` and `export_image_format="AUTO"`.
  `result.tangents` says so, and the suite asserts `TANGENT` on every mesh primitive in
  the written glTF.
- **`-loop` and `-col`, but no `-lod`.** Clip names are passed through untouched, so the
  `-loop` convention survives to the import script. Meshes suffixed `-col` / `-colonly` /
  `-convcol` are exported as-is for Godot's own collision handling and listed in the
  result. **`-lodN` is not a Godot convention** — its import suffixes are `-col`,
  `-convcol`, `-occ`, `-navmesh` and friends, and there is no `-lod` among them; Godot 4
  generates LODs on import with meshoptimizer instead. So:
  - **`lods: "auto"` (the default)** exports only the unwrapped LOD0 and lets Godot's
    importer do the levels. Generated `<mesh>_lod1` / `_lod2` siblings are left out, and
    the result says which ones and why.
  - **`lods: "manual"`** exports them too, under **their own names** (`Sculpt_lod1`, not a
    fake `-lod1` suffix), and writes a `LOD_CHAIN` into the import script that sets
    `visibility_range_begin` / `visibility_range_end` on each sibling — because without
    those ranges every level renders at once, on top of each other. The bands are
    contiguous and strictly increasing, LOD0 begins at 0 and the last level ends at 0
    (Godot's "for ever"); the distances come from each level's measured error (see *LOD
    budgets* above), or are measured at export time for a level with no record.
    `result.lod_chain` carries the same `{mesh, begin, end}` list. Legacy `lods: true` /
    `false` still work and mean `manual` / `auto`.
- **LODs get weights.** An LOD is decimated in stage 2, *before* skinning, so it arrives
  at export with tags but no deform weights — which makes the glTF exporter invent a
  `neutral_bone` joint and hang the geometry off it, shipping an LOD that never animates.
  Missing weights are transferred from the skinned mesh by nearest vertex (distance
  weights if there is no skinned sibling), and you get a warning saying so.
- **Root motion** (`root_motion: true`) moves each clip's horizontal hip travel onto the
  `root` bone: the hips' object-space matrices are sampled first, then root is keyed with
  the travel and the hips re-keyed with it removed, so the visual result is identical and
  Godot gets a root-motion track. If it fails for a clip, the clip still exports with the
  travel on the hips and the result says why — it is never allowed to lose an export.
- **Nothing is left behind.** Every temporary object lives in one collection removed in a
  `finally`, along with the baked actions and every temporary rename. A *failed* export
  leaves the scene exactly as it found it; the suite asserts that too.

**The Godot import helper.** `<name>_import.gd` is written next to the glTF: a Godot 4.x
`EditorScenePostImport` script that walks the imported scene's `AnimationPlayer`, sets
`loop_mode = Animation.LOOP_LINEAR` on every animation whose name ends in `-loop` (and
`LOOP_NONE` on the rest), prints what it found, and warns about any clip this export
contained that Godot did not see.

**Importing it in Godot** (4.x):

1. Copy `character.glb` and `character_import.gd` into the project.
2. Select `character.glb` in the FileSystem dock, open the **Import** tab.
3. Set **Import Script** to `character_import.gd`, press **Reimport**.
4. The scene arrives as a `Skeleton3D` (deform bones only, plus `root`), the meshes, and
   an `AnimationPlayer` whose `-loop` clips already loop. Drop it into a scene and drive
   it with an `AnimationTree`.

### Cloth (Phase 5, stage 5)

`RigForge ▸ Cloth` (`rigforge_cloth`). Toggle the tags the garment covers (or switch on
**Use Selection**), pick a preset and an output, set offset and thickness, press **Make
Garment**.

**What it builds.** The tagged faces are duplicated into a new object, every vertex is
pushed out along its own normal by `offset_mm`, the sheet is subdivided if it is too
coarse to bend, and Solidify gives it `thickness_mm` of shell. The duplicate keeps the
body's **vertex groups** (so the deform weights arrive for free — Solidify and the
subdivision interpolate them) and its **material slot layout** (empty is fine). The result
is tagged `tag_Garment` and remembers the body it came from in `forge_garment_of`.

**The three outputs.**

| `output` | what happens | what ships |
|---|---|---|
| `skin_tight` | no simulation at all | the garment, weighted to the same rig, with an Armature modifier |
| `shapekeys` | Cloth + a Collision modifier on the body, the sim stepped for `frames`, the settled coordinates baked into a `Settled` shape key at value 1.0 — then **both modifiers removed** | the same skinned mesh, plus one morph target |
| `bones` | **not implemented in v1.** Warns loudly and does `skin_tight` instead | as `skin_tight`, with `requested_output: "bones"` in the result |

Godot does not run Blender cloth, so the bake is what ships. The cloth modifier, the
collision modifier and their point caches are gone before the command returns, whether the
sim worked or not.

**Cloth presets.** These are Blender's own shipped presets, not invented numbers, so the
Physics tab shows you something you recognise afterwards. `mass` is kg per square metre;
the stiffnesses are Blender's unitless spring constants; `quality` is solver substeps per
frame, and is the single most effective knob against an exploding sim.

| preset | Blender preset | mass | tension | compression | shear | bending | damping (T/C/S/B) | air | quality |
|---|---|---|---|---|---|---|---|---|---|
| `cotton` | Cotton | 0.30 | 15 | 15 | 5 | 0.5 | 5 / 5 / 5 / 0.5 | 1.0 | 5 |
| `leather` | Leather | 0.40 | 80 | 80 | 80 | 150 | 25 / 25 / 25 / 0.5 | 1.0 | 7 |
| `heavy` | Denim | 1.00 | 40 | 40 | 40 | 10 | 25 / 25 / 25 / 0.5 | 1.0 | 8 |

**Keeping the sim on its feet.** A cloth sim over a duplicated sculpt patch is the easiest
thing in this whole pipeline to blow up, so four things are done about it:

- **The seam is pinned.** Every vertex on an open edge of the pre-solidify sheet goes into
  a `Forge Pin` group used as the cloth `vertex_group_mass` at `pin_stiffness` 1.0. Without
  it a shirt slides off the shoulders on frame 2.
- **Self collision is off by default** (`self_collision: true` to turn it on). A patch
  duplicated off a sculpt is very often already self-intersecting, and a self-collision
  solver handed a tangled mesh is the most reliable way to make it explode.
- **Collision quality 5**, with the collision distance scaled to the model
  (`min(thickness/2, 1% of the body's longest axis)`), on both the garment and the body's
  Collision modifier.
- **The result is measured.** Non-finite coordinates, or a settled bounding box more than
  **3×** the body's on any axis, mean the sim exploded: no shape key is baked, the
  un-simulated garment is delivered instead, and you get a warning naming the axis and the
  numbers. `result.sim` always reports `ok`, `reason`, `bbox` and how long it took.

**Skinning.** Weights are inherited by duplication, then anything still unweighted is
filled from the body by nearest-vertex data transfer (distance-to-bone as a last resort),
then limited to 4 influences and normalised. `result.weights.method` says which of those
ran. With no rig bound to the body the garment is still built, unskinned, with a warning.

**Limits.** One garment per call. The garment carries the body's `tag_*` groups as well as
`tag_Garment` — harmless, and useful if you retopologise it later. Shape keys on the body
are *not* carried onto the garment (you get a warning). The sim runs from frame 1 to
`frames` regardless of the scene's own frame range, and the scene's range and current
frame are restored afterwards.

### The action library (Phase 5, stage 6)

`RigForge ▸ Actions` (`rigforge_action`). The list shows every action in the file with its
frame range, a loop badge, and an NLA marker; the radio button on the left assigns one to
the rig so it is the one you are editing.

**The `-loop` convention is enforced in both directions.** `loop: true` appends `-loop` if
it is missing, `loop: false` strips it if it is there, and omitting `loop` leaves the name
exactly as given. That is the same suffix the Godot import script reads to set
`Animation.LOOP_LINEAR`, so `idle` and `idle-loop` are not a naming preference — they are
different behaviour in the engine.

| `action` | needs | does |
|---|---|---|
| `new` | `name`, `loop?`, `rig?` | creates the action and assigns it to the rig. A name that is already taken is **refused**, not silently suffixed `.001` |
| `list` | `rig?` | every action with `frame_range`, `loop`, `bones`, `fcurves`, `nla` + `nla_tracks`, and (with a `rig`) `fits_rig` / `assigned` |
| `delete` | `name` | removes it, clearing it off any object holding it; NLA strips that went with it are reported |
| `duplicate` | `source`, `name?`, `loop?` | branches a copy (default name `<source>_copy`) |
| `rename` | `source` + `name`, **or** `name` + `loop` | moves an action, or just applies/removes the `-loop` suffix in place |
| `push_nla` | `name`, `rig` | pushes the action onto its own NLA track named after it and clears the active action, the way Blender's own Push Down does |

Deleting an action that does not exist is a clean error listing the ones that do (with a
"did you mean" when the name is close). Every call returns the whole library in `actions`.

### Described-motion keyframing (Phase 5, stage 6)

`rigforge_keyframe` is the command Claude uses when you say *"a heavy two-beat hop, ears
trail"*: one structured call instead of a page of raw Python.

```jsonc
{"type": "rigforge_keyframe", "params": {
   "rig": "Sculpt_retopo_rig", "action": "hop", "interpolation": "LINEAR", "clear": true,
   "keys": [
     {"bone": "torso",          "frame": 1,  "rotation_euler_deg": [0, 0, 0], "location": [0, 0, 0]},
     {"bone": "torso",          "frame": 12, "rotation_euler_deg": [-8, 0, 0], "location": [0, 0, -0.05]},
     {"bone": "upper_arm_fk.L", "frame": 12, "rotation_euler_deg": [0, 0, 30]}
   ]}}
```

- Each key needs a `bone` and a `frame` plus at least one of `rotation_euler_deg`,
  `location`, `scale`. A key that sets nothing is an error naming the bone and the frame.
- **Everything is validated before anything is written.** A typo in `keys[7]` must not
  leave `keys[0..6]` half applied.
- **Rotations are degrees, euler, in the bone's own space.** A bone in quaternion mode
  (which is what every Rigify control ships as) is switched to `XYZ` and the switch is
  reported in `rotation_modes` — describing a pose in quaternions is not a thing anybody
  does out loud, and silently ignoring the mode would be worse than saying so.
- **`interpolation`** is applied to the keyframe points this call made, and only those.
- **`clear: true`** wipes the action's F-curves first (Blender 5.0 keeps them in per-slot
  channelbags, not `action.fcurves`; the add-on handles both).
- **`fk_switch`** (default `"auto"`) moves Rigify's `IK_FK` blend to full FK and keyframes
  it whenever any key targets a `*_fk` bone. Without this the pose looks perfect in the FK
  controls and exports as a T-pose, because Rigify limbs default to IK.
- **A bone it cannot find is answered with the one it meant**: closest matches first, then
  up to 30 **control** bone names (machinery — `DEF-`, `ORG-`, `MCH-`, `WGT-`, `VIS-` — is
  listed only if the rig has nothing else). Error messages here have to be actionable
  because this is the command an agent retries against.

### Retargeting a mocap clip (Phase 5, stage 6)

`RigForge ▸ Actions ▸ Retarget` (`rigforge_retarget`). Point **Clip** at a `.bvh` or
`.fbx` **you supply** — a Mixamo download you already have, a capture, anything — name the
action, press **Retarget Clip**. Both importers ship inside Blender. **Nothing is ever
fetched over the network**, and a missing file says so in the error.

**How the transfer works.** Not matrix arithmetic: a constraint bake.

1. The clip is imported into a temp collection (`FORGE_RETARGET_TEMP`).
2. `scale: "auto"` measures both skeletons' rest heights and scales the source object by
   the ratio; the source is then shifted so its hip bone's rest position sits on the rig's.
3. Every mapped control gets a world-space **Copy Rotation** from its source bone, and the
   hips control additionally gets a world-space **Copy Location** — that is the clip's
   travel, already scaled, with no offset arithmetic to get wrong.
4. `nla.bake(visual_keying=True, only_selected=True)` over the clip's own frame range,
   with **only the mapped controls selected**. Blender's evaluator does the rest-orientation
   algebra, which is why this is more robust than solving local rotations by hand — and
   baking *only* the mapped bones matters, because a Rigify rig is full of constrained
   `MCH-`/`ORG-` bones whose evaluated transform, written into `matrix_basis` while the
   constraint still runs, would be applied twice.
5. Constraints come off, the import is deleted, the clip's own action is purged, and the
   scene's frame range is restored — all in a `finally`, so a failed retarget leaves the
   file exactly as it found it.

**FK, on purpose.** The motion lands on the rig's **FK chains** so the result is something
you can open and fix. Rigify limbs default to IK, so the rig's `IK_FK` properties are moved
to full FK and **keyframed into the baked action** (the Godot export bakes per action, so a
switch that was only a live property would be whatever the last command left it at).

**The mapping table.** `mapping: "auto"` is a case-insensitive fragment match, tried in
this order — the order *is* the design, because `LeftForeArm` contains "arm" and
`LeftUpLeg` contains "leg":

| source name contains | slot | target control (first that exists on the rig) |
|---|---|---|
| `forearm`, `fore_arm`, `lowerarm`, `radius`, `elbow` | forearm | `forearm_fk.{L,R}`, `forearm.{L,R}` |
| `upperarm`, `upper_arm`, `humerus`, `shldr` | upperarm | `upper_arm_fk.*`, `upper_arm_ik.*`, `upper_arm.*` |
| `shoulder`, `clavicle`, `collar` | shoulder | `shoulder.{L,R}` |
| `hand`, `wrist` | hand | `hand_fk.*`, `hand_ik.*`, `hand.*` |
| `upleg`, `upperleg`, `thigh`, `femur` | thigh | `thigh_fk.*`, `thigh_ik.*`, `thigh.*` |
| `lowerleg`, `foreleg`, `shin`, `calf`, `tibia`, `knee` | shin | `shin_fk.*`, `shin.*` |
| `toebase`, `toe`, `ball` | toe | `toe_fk.*`, `toe.*` |
| `foot`, `ankle` | foot | `foot_fk.*`, `foot_ik.*`, `foot.*` |
| `head` | head | `head`, `tweak_spine.005` |
| `neck` | neck | `neck`, `spine_fk.003`, `tweak_spine.004` |
| `upperchest`, `chest`, `spine2`, `spine3`, `thorax` | chest | `chest`, `spine_fk.002`, `tweak_spine.002` |
| `spine1`, `abdomen`, `waist`, `spine` | spine | `spine_fk.001`, `hips`, `tweak_spine.001`, `chest` |
| `hips`, `hip`, `pelvis`, `root` | hips | `torso`, `hips`, `spine_fk`, `root` — and this is the bone whose travel is copied |
| `arm` (fallback) | upperarm | as above |
| `leg` (fallback) | shin | as above |

Sides come from `left`/`right` in the name, a `.L`/`.R`/`_L`/`_R` suffix, or a bare leading
`l`/`r` (CMU-style `lfemur`). Prefixes like `mixamorig:` and `bip01 ` are stripped.
**Refused on purpose:** anything containing `finger`, `index`, `middle`, `thumb`, `ring`,
`pinky`, `eye`, `jaw`, `tongue`, `breast`, `_end`/`nub`/`site` — five finger joints all
folding onto one hand control is worse than five reported gaps. Two source bones cannot
claim the same control; the second is reported as unmapped with the reason. Everything not
mapped comes back in `unmapped` (names) and `unmapped_detail` (name + reason).

Pass an explicit `{"SourceBone": "target_bone"}` dict as `mapping` when the clip uses names
the heuristic does not know. A target that is not a bone of the rig is an error with the
usual "did you mean".

**Limits.** The transfer is a world-space rotation copy, so it is approximate where the two
skeletons' rest orientations disagree — expect to polish wrists and shoulders. `.fbx`
imports its whole scene (meshes included) and everything it brought is deleted afterwards;
only the armature is used. `replace: true` (the default) overwrites an existing action of
that name and says so in the warnings — the mapping is something you iterate on.

### What still wants your hands

- **Weights at the shoulders and hips.** The cleanup makes them defensible, not
  beautiful. Nothing is locked: it is a normal Blender weight paint session afterwards.
- **The rest pose.** The fitter matches the sculpt's pose; a sculpt in a wild pose gets a
  wild rest pose. A-pose or T-pose sculpts fit best.
- **Faces and fingers.** They are scaled with the head and hand, not fitted — there are no
  landmarks in a `Head` tag that say where an eyelid is. Tag them and use
  `preset: "human"` only if you intend to animate them.
- **Garment silhouette.** The sim settles cloth onto a body in its rest pose; it does not
  know the character is about to run. Sleeves and hems are where the shape key wants an
  eye, and a stiffer preset with fewer frames is usually the fix.
- **Retargeted wrists and shoulders.** A world-space rotation copy between two skeletons
  that disagree about rest orientation lands close, not exact. The clip is on FK controls
  precisely so you can fix it.

## Layout

```
addon/forge/
  __init__.py            bl_info, add-on preferences, register/unregister
  prefs.py               add-on id + safe preference access
  server.py              TCP server, main-thread pump, start/stop operators
  tools/registry.py      command registry + error wrapping
  tools/common.py        every protocol command
  tools/diagnose.py      mesh_diagnose: the defects, each with a place in millimetres
  tools/verify.py        the geometric gate: verify_design (the scored, tier-stamped
                         report) and turntable (the 24-view judging rig)
  tools/silhouette.py    fit_to_silhouette: warp a sculpt until its outline matches
                         one reference picture per orthographic view (Phase 18b)
  tools/partforge.py     PartForge state, HTTP client, operators
  tools/rigforge.py      RigForge tags, manifest, retopo, auto-UV, panel state + operators
  tools/rigforge_rig.py  RigForge metarig fitting, Rigify generate, weights, Godot export
  tools/rigforge_joints.py  predicted-joint hints: the metarig fitter's third landmark
                         source (loads forge.joints/1, matches roles, rations belief)
  tools/rigcheck.py      the deformation harness: rig_check poses every joint to its
                         extremes and measures volume / clipping / twist collapse
  tools/rigforge_anim.py RigForge cloth, the action library, keyframing, retargeting
  tools/assistant.py     Assistant chat state, bridge client, operators (Phase 6)
  tools/flows.py         Flows: the JSON format, the runner, flow_list/flow_run, the box (6b)
  tools/model.py         Downloaded models: check_model/segment_model, the Model box (6d),
                         and merge_for_print + its button (Phase 11)
  tools/curves.py        The drawn door: profile_from_curve / outline_from_curve (Phase 11)
  tools/services.py      The health row, Start services, Revert last AI action
  ui/panels.py           sidebar panels
  blender_manifest.toml  extension metadata (Blender 4.2+ install path)
addon/tests/
  headless_phase2.py     headless checks for the Print Checks / Segments panels
  headless_rigforge.py   headless checks for the RigForge tag/manifest/retopo/UV stack
  headless_phase4.py     headless checks for the rig, the weights and the Godot export
  headless_phase5.py     headless checks for cloth, actions, keyframing and retargeting
  headless_rigbridge.py  headless checks for the rigging bridge (port 9901): joints_file
                         blending against a hand-written detector file, the frame gate,
                         and rig_check on the generated rig - no GPU, no download
  headless_assistant.py  headless checks for the Assistant panel against a fake bridge
  headless_flows.py      headless checks for flow_list/flow_run and the Flows box
  headless_reference.py  headless checks for load_reference and the attach field (6c)
  headless_preview.py    headless checks for render_preview: real PNGs, framing measured
                         on the pixels, all four views, and the scene put back exactly
  headless_verify.py     headless checks for the geometric gate (port 9900): every claim
                         tier-stamped (walked structurally, not spot-checked), the
                         symmetry residual ordering, UVs present/absent/flipped, the poly
                         budget, silhouette IoU against a synthetic reference (a cube's
                         own render, used AS the reference), the confidence drop on a busy
                         background, loops from an armature and from tag boundaries, and
                         the turntable contact sheet measured on its IHDR
  headless_silhouette.py headless checks for fit_to_silhouette (port 9904): a sphere
                         fitted to ellipses it draws itself, with the right answer in
                         millimetres known by arithmetic; the untouched axis asserted
                         bit-identical; front+side together; every knob; the mask tiers;
                         thirteen refusals; and flow legality proven by running a flow
  headless_ui_batch.py   headless checks for the UI batch: undo checkpoints, the health
                         row, the chips, the empty states, check_model/segment_model
                         against a fake service, and the flow editor
  headless_phase11.py    headless checks for the drawn door (profile/outline_from_curve),
                         the component convention, the voxel argument, merge_for_print,
                         the Merge for Print button, and the merge-and-check and
                         sculpt-ready flows
```

`rigforge_rig.py` holds the Phase 4 commands but keeps its panel state in
`rigforge.py`'s `ForgeRigForgeProps` and reports through the same `status` line, so the
whole RigForge section behaves as one panel. `rigforge_anim.py` (Phase 5) keeps its own
`ForgeAnimProps` on the scene instead — cloth and the action library have a lot of state
and none of Phase 3's fields mean anything to them — but it reports through a `status` line
drawn the same way, so the section still reads as one panel.

Two conventions in `ui/panels.py` worth knowing before you edit it: the PartForge panels
bind their state to a local called `props`, the RigForge Phase 3/4 ones to `rf`, the
Phase 5 ones to `ra`, the Phase 6 Assistant to `chat`, the Phase 6b Flows box to `fl`, the
Model box to `md` and the status row to `sv`.
They are different
PropertyGroups on the scene, and the headless panel-wiring tests tell them apart by that
name — reuse one and another phase's suite fails on a property that is not on its group.
And nothing in the file does work: every button is an operator that reports back through
its panel's `status` string.

## Headless tests

Eighteen suites, all `--background` only. Never launch Blender windowed to run them. As
of floor plans they are **49 + 108 + 156 + 150 + 78 + 40 + 97 + 92 + 156 + 44 + 69
+ 316 + 139 + 79 + 108 + 163 + 105 + 208 = 2157 checks**, all green on Blender 5.0.1.

### Phase 19 — floor plans to prototype levels (`headless_floorplan.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_floorplan.py
```

Socket port **9905**. **278 checks**, one Blender launch, no window, no service, no network
beyond loopback and no files on disk: the plan is a Python dict in the file and every right
answer is arithmetic. The fixture is a two-room flat, 7000 × 3000 mm, split by a party wall
at x = 4000 with one 820 × 2040 mm door centred at 1500 mm along it — so the door runs
1090…1910 mm, the wall builds as exactly **three boxes** (a pier either side, one header
over the door) and that is **24 vertices and 18 faces**, asserted rather than eyeballed.
Covering:

- **registration and undo** — registered, deliberately **not** read-only, undo step named
  `Forge: build_floorplan`, and the fingerprint itself proved to ignore key order and to
  notice a changed millimetre;
- **the first build** — eight objects, per-object vertex and face counts, millimetre
  dimensions (7000 × 100 × 2400 for a wall, 4000 × 3000 × 50 for a slab, 600 × 600 × 850
  for the washer/dryer), the door cutout's exact X breaks (0, 1090, 1910, 3000) and Z
  breaks (0, 2040, 2400), **not one vertex inside the void the door cut**, the slab hanging
  below z = 0, the three shared materials, a wall's origin at its `from_mm` end rotated
  along its own centreline, and a revolute mechanism record whose origin is the hinge edge
  on the centreline;
- **THE INCREMENTAL LAW, four ways, each on `as_pointer()` identity rather than on the
  report** — one wall moved rebuilds exactly one mesh datablock while all eight objects
  stay the same allocation; calling again with the same plan is a total no-op (objects
  *and* meshes byte-identical allocations, and the notes say so); an added fixture only
  creates; a removed one only deletes; and a raised `defaults.ceiling_mm` rebuilds all five
  walls while the two slabs and the fixture are not touched at all;
- **never clobber** — a hand-scaled fixture whose plan entry then changes is *kept*, with
  a warning naming the object and a reason saying it was scaled, its scale still on it and
  its mesh never replaced; an object carrying `forge_fp_keep` is kept; an `FP:` object
  Forge did not build is kept, and is **not deleted** when its id leaves the plan either;
  and an `FP:` name already taken by an empty is kept while the other seven build anyway;
- **rebuild** — every rebuilt object is a genuinely new object (proved with a marker custom
  property, not with `as_pointer()`: the allocator is entitled to hand a freed address
  straight back), nothing is reported as deleted because a rebuilt id is not a removed one,
  and `forge_fp_keep` still holds with a warning saying the marker outranked the mode;
- **the knobs** — `floor:false` builds six objects instead of eight and says why, turning
  floors back on is an *addition* of two slabs and nothing else, turning them off again
  deletes only the slabs, and `collection` puts the level wherever it is pointed;
- **windows, gaps and an L** — a window leaves a sill *and* a header (Z breaks 0, 900,
  2100, 2400; four boxes), a gap runs floor to ceiling and splits its wall into two
  separate piers, and a six-cornered L-shaped room gets a properly tessellated slab
  (4 triangles a cap, not a fan);
- **twenty-one refusals**, each with a sentence and each proved to have built nothing:
  duplicate ids, an opening wider than its wall, a label with no footprint, an unknown kind
  and an unknown mode (both with the `difflib` near miss), an opening off the end, two
  openings overlapping, a zero-length wall, a two-corner room, a collinear room, the wrong
  units, an empty plan, a plan that is not an object, a wall with one end, a non-numeric
  coordinate, a missing id, an id too long for a Blender name, an unknown swing, `at_mm`
  and `start_mm` at once, a footprint with no area, and no plan at all;
- **flow legality proven by running one** — a two-step flow (`ping`, then the build) with
  the collection arriving through a `{{param}}`;
- **the budget** — 40 walls and 40 doors in **0.047 s**, re-diffed in under a millisecond;
- **reconcile, the return channel** — an untouched level comes back with all eight ids
  `clean`; a wall dragged 500 mm north is measured to the millimetre (new centreline, the
  old one beside it, the fields that changed and how far it went, `mesh: "intact"`); a
  fixture spun 90 degrees comes back as **exactly** 90 and a wall scaled twice as thick
  comes back as a resize with its centreline unmoved; a box resized in Edit mode is
  measured off its own mesh and marked `edited-but-still-a-box`; a **sculpted** placeholder
  is unabsorbable and the reason recommends `forge_fp_keep`; a wall tilted 12 degrees and a
  slab lifted off the floor are both unabsorbable, because a floor plan is top-down; a
  deleted wall is `deleted_in_scene` with what it was, while an object merely moved to
  another collection is reported as *outside* it and never as a deletion; a stranger box is
  a **candidate** with its bounding box measured and its rotation left unknown; a plan edit
  that has not been built yet is `stale` rather than measured, and a plan edit *plus* a hand
  edit is a refusal saying to build first; three refusals (no plan, no such collection, a
  collection with no `FP:` objects in it); and **the read-only guarantee twice**, on
  `as_pointer()` identity, on every world matrix, and on the object/mesh/collection counts.

### Phase 18b — silhouette fitting (`headless_silhouette.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_silhouette.py
```

Socket port **9904**. **105 checks**, no window, no service, no network beyond loopback,
and no reference pictures on disk that the suite did not draw itself. Every fixture's
right answer is arithmetic rather than a golden file: a 1 m sphere is 2000 mm across, so
fitted to an ellipse whose bounding box is 201 × 401 px with `fit="height"` the answer is
`2000 × 201 / 401` = **1003 mm** wide, **2000 mm** tall and **exactly 2000.000 mm** deep.
Covering:

- **registration and undo** — registered, deliberately **not** read-only, undo step named
  `Forge: fit_to_silhouette`, and the three view frames (front → XZ/depth Y, side →
  YZ/depth X, top → XY/depth Z) asserted as tuples rather than trusted;
- **the single-view fit** — the mm dimensions above, IoU 0.497 → 0.990, mean outline error
  313 mm → 2.5 mm, the shape-only IoU rising too (so framing cannot explain it), and both
  numbers tiered `measured`;
- **the untouched axis** — `displacement_by_axis_mm["Y"] == 0.0` *and*
  `numpy.array_equal` on the Y column, because "near enough" is not what the contract
  says;
- **a wider reference widens** — the fit follows the picture, not a preference;
- **front + side** — two references, three axes, X from the front, Y from the side, Z
  agreed between them, both views above 0.88 IoU;
- **the shorthand** — `front_image` / `side_image` becomes the same two views, and giving
  both spellings at once is refused rather than merged;
- **`strength=0` is an exact no-op** — not one coordinate moves, the mesh is not written
  to at all, and before/after are the same number, which is how you get the measurement
  without committing to the fit;
- **the knobs** — `strength` as a lerp, `falloff` holding the interior (98.3 mm → 9.7 mm
  while the outline still lands), `symmetry` pulling a lopsided reference back onto its
  own mirror (residual 61.4 mm → 0.00 mm, measured with `verify.symmetry_residual`),
  `iterations` recovering what heavy smoothing gives away (0.941 → 0.988 at `smooth=0.8`),
  and a deliberate 350 mm spike surviving `smooth=1.0` — **the mesh is never smoothed**;
- **the mask machinery** — alpha `measured` / high confidence, flat background
  `heuristic` with the PLAIN BACKGROUND caveat, an explicit `threshold` honoured, and a
  single stray pixel warned about rather than cropped;
- **shape keys** counted and warned about;
- **thirteen refusals**, each with a sentence: no file at the path, a misspelled view
  (with the `difflib` near miss), a view with no axis, a view with no image, no references
  at all, an empty list, two views of the same side, a threshold out of range, a strength
  out of range, an object that is not there, a camera instead of a mesh, and a picture
  with nothing in it;
- **flow legality proven by running one** — a two-step flow (`ping`, then the fit) rather
  than a lookup in a registry list;
- **the budget** — 8 066 vertices, two references, four passes, **0.33 s**.

### Phase 17 — mechanism demos (`headless_mechanism.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_mechanism.py
```

Socket port **9903**. **163 checks**, no window, no service needed. All three commands are
tested in **one** launch on purpose: every extra `--background` run is another flash on
the artist's machine. Covering:

- **`animate_object` round trips** — the F-curves exist, they hold the asked-for values at
  the asked-for frames (`location_mm: [0,0,-1.8]` evaluates to −0.0018 m at frame 8), the
  interpolation lands on the points *this call* made, `clear` really empties, a quaternion
  object is moved to XYZ euler and says so, and a near-miss name comes back with *"Did you
  mean 'Flame'?"*. A typo in the **last** key is asserted to leave the first one unapplied;
- **`set_material_emission`** — a fresh `Forge Glow` emission material wired to the
  material output, an existing Principled reused through its own emission inputs, and the
  keying that matters: dark at frame 1, lit at frame 8, and **still dark at frame 7**,
  because an LED does not fade up;
- **`render_animation` writes a real `.mp4`** — ISO base-media `ftyp` magic, a `mvhd`
  duration parsed out of the box tree that matches the frames and fps asked for, the exact
  path requested (asserted by the *absence* of a `press0001-0012.mp4` beside it), and the
  camera framed over the whole clip rather than frame one;
- **the scene comes back exactly as it was** — engine, output path, resolution, file
  format, **media type**, ffmpeg container/codec/CRF, fps, frame range, frame position,
  colour management, Workbench shading, `scene.camera` and every object's `hide_render`,
  with no camera and no light datablock left behind;
- **the end-to-end demo** — a flame pressed 1.8 mm over 12 frames, an LED keyed on at the
  latch frame, and a film of the two happening together;
- **the budget**, measured and printed by the suite: 48 frames at 640 px on EEVEE in
  **52.8 s with a cold shader cache and 6.9 s with a warm one**, asserted against a
  deliberately loose 180 s upper bound (this asserts "not a coffee break", not a
  benchmark, and the first EEVEE render of a session is allowed to pay for shader
  compilation).

### Phase 15 — project `.blend` files (`headless_projects.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_projects.py
```

Socket port **9899**. **79 checks**, no window, no service needed.
`FORGE_PROJECTS_DIR` is pointed at a temp folder before anything runs, so a suite that
writes `.blend` files can never leave one in the repo's own `projects/`. Covering:

- **the copy, which is the whole feature** — the file lands at
  `projects/<name>/<name>.blend`, the folder is created when missing, and
  `bpy.data.filepath` is the *same string* before and after. Saving into the file you
  currently have open is checked separately, because that is the case where a retarget
  would be easiest to miss;
- **the refusals**: a name with a slash in it, `create: false` on a folder that is not
  there (and the folder is not made anyway), a `project` that is not a string, an open of
  a project with no `.blend` (which names the button that makes one);
- **the confirmation round trip** — with unsaved work and no `confirm`, `needs_confirmation`
  comes back with a sentence and a count, and *nothing in the scene moves*;
- **the question this feature stood or fell on: does the socket server survive
  `wm.open_mainfile`?** A file load clears Blender's timer list and the add-on's
  main-thread pump *is* a timer, so if it went with the file every Forge command after an
  open would hang forever on a queue nothing drains. The suite registers a second,
  deliberately **non-persistent** timer beside the pump and asserts, after the open, that
  the control is **gone** and the pump is still there — without the control, "the pump
  survived" could only mean "nothing was ever cleared". It then checks the server is the
  same object on the same port, that `_pump()` still finds a live server, and round-trips
  `get_scene_info` and `ping` through the real socket afterwards. **Measured answer: yes,
  it survives** — the persistent flag is what saves it;
- **the "do not ask" branch**, reached the only way it can be reached headless: an empty
  scene. `--background` leaves `bpy.data.is_dirty` true from the first line of the session
  and an explicit save does not clear it (measured on 5.0.1), which is exactly why the gate
  is not that flag alone;
- **undo classification** — `push_undo` declines both commands — and the PartForge box's
  **Save Scene to Project** button, pressed for real and asserted to have written the file
  and reported it in the panel's own status line.

### Phase 11 — drawn base shapes and merge-for-print (`headless_phase11.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_phase11.py
```

Socket port **9894**. **139 checks**, no window, nothing outside loopback — the geometry
service on 8765 is consulted read-only (`/generate`) for one end-to-end proof and its
absence is a printed note, never a failure. Covering:

- **the drawn door as a measurement, not a trace** — a vase Bezier built in-script is
  sampled into 7 points with `z` strictly increasing and every radius ≥ 0, and *both* of its
  curvature extremes — the bulge and the waist — are asserted to survive the reduction,
  measured against the sampler's own dense samples rather than against the drawn control
  points;
- **the options** — `close_bottom` standing the body on `z = 0` (and reporting the shift),
  a silhouette drawn to the LEFT of the axis giving the same body, a curve drawn in the side
  view read as `YZ`, and a 3-point poly curve resampled up to `soft_body`'s minimum *with*
  the note that says so;
- **the refusals** — a mesh, a name that is not in the file, no name at all, a point count
  outside the helper's band, two strokes in one object, a stroke with no height; and for
  outlines, an open curve (naming `Alt+C`), a loop closed by hand being accepted anyway;
- **the end-to-end proof** — the sampled points POSTed to the live service as a `soft_body`
  script, asserted to build a watertight solid at the height the artist drew;
- **the component convention** — `component_name` / `split_component_name` /
  `common_project` / `project_of`, including the 63-byte cap and a project whose own name
  has a dash in it;
- **the voxel argument** — nozzle/2 from the real `templates/printer.json`, the prediction,
  the coarsening at the million-face cap with its note, and both clamps;
- **merging for real** — two overlapping cubes and a sphere become ONE watertight object,
  all three inputs counted as sealed, the originals hidden and still present, the shell left
  active, and the face-count prediction asserted within a factor of the truth (measured:
  predicted 139 945, got 99 070 — 0.71×);
- **Ctrl+Z** — `bpy.ops.ed.undo` really removes the merged shell and unhides the pieces;
- **the collection route** — a project collection with one proposal hidden ("scrap the left
  ear") merges the survivors only, names the skipped ones, lands in the project's
  collection and leaves the scrapped piece untouched;
- **the options and refusals** — an explicit voxel and name, `keep_originals: false` really
  deleting, a shell named after one of its own pieces refused, an unknown collection refused,
  nothing-to-merge refused with the three ways in, and the selection used when nothing is
  named;
- **the button** — drawn in the Model box, the selection count in its hint, pressing it
  really merging, the Model box pointed at the result, and a plain refusal (not a traceback)
  with nothing selected;
- **the flow** — `flows/merge-and-check.json` loads, validates, has both ops registered,
  aims the check at `{{steps.0.result.object}}`, and its merge step replays through the real
  flow runner;
- **the other flow** — `flows/sculpt-ready.json` (remesh → Sculpt Mode → brush) replays
  whole against a sphere: the topology really changes and the object really ends up in
  Sculpt Mode, which is the base-shape handoff as one press.

### Phase 8 — the workspace copilot and buddy mode (`headless_workspace.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_workspace.py
```

Socket port **9892**, fake assistant bridge on **9893**. No geometry service, no Claude
CLI, no network beyond loopback, no window. **316 checks** covering:

- **registration** — nine commands, every one READ-ONLY, `push_undo` refusing each;
- **the headless guard** — the five view commands and `capture_viewport` refusing with
  `workspace.NO_VIEWPORT`, character for character, and no traceback. The suite first
  *proves* that `--background` does build a VIEW_3D, which is why the guard is on
  `bpy.app.background` rather than on "is there an area";
- **the window-dependent core, twice** — `apply_view`, `apply_shading`, `apply_overlays`
  and `apply_framing` driven against the real off-screen `SpaceView3D` (which proves the
  property names are this build's) *and* against stubs (which proves the guards when a
  build is missing half of them). Front really is Blender's front quaternion, and the
  three axis views really are the same three angles `render_preview` points its camera at;
- **the validation matrix** — twenty refusals, each asserted to name why and to carry no
  traceback, including that `sculpt_brush(size=0)` says the size is out of range rather
  than something about brushes, and that `mesh_diagnose(examples=0)` says so rather than
  something about objects;
- **`set_mode` for real** — the mode really changes, Sculpt Mode on an armature is refused
  by name *before* Blender is asked and leaves the artist where they were, Pose Mode on a
  mesh is refused, asking twice is a success, and `"vertex paint"` is understood;
- **`sculpt_brush` for real** — the catalog is Blender's own 62 essentials brushes (not
  the one datablock a fresh file holds), `"clay strips"` really becomes Clay Strips in
  `tool_settings.sculpt`, size/strength/symmetry land, dynamic topology toggles, and
  `"Smoothify"` comes back suggesting `'Smooth'`;
- **the context payload** — a scene with two selected objects, the mode, no brush in
  Object Mode, the brush with its size and strength in Sculpt Mode, and the 10-object cap
  with its "... and N more" line;
- **`mesh_diagnose` on a deliberately broken mesh** — one object carrying two boxes that
  pass through each other, a box with its lid off, a crammed patch, a starved plate, an
  ngon, a zero-area triangle and a loose vertex, every one asserted to be *found* and
  *located* within tolerance of where it was planted. Plus an evenly tessellated icosphere
  asserted CLEAN, so the suite proves it does not manufacture problems, and the scale
  checks on a non-uniform and a sub-millimetre object;
- **the density map on its own** — synthetic areas where the answer is known, including
  that two stray faces are not a region and an even mesh has no regions at all;
- **the buddy's gating** — the change hash (one nudged vertex changes it; a non-mesh has
  none; two objects never collide), `should_check`'s four outcomes, and `buddy_tick`
  driven *by hand* through busy-skip, unchanged-skip, one-turn-on-change, the previous
  note being carried forward, and off meaning off;
- **check-my-work shaping** against the fake bridge — the message led by the fixed line,
  the render path really on disk, the Read instruction, the workspace block, the mesh
  numbers, the missing screenshot said out loud rather than dropped, both turns marked as
  check-ins and the outgoing turn logged as the short sentence.

### Phase 7 — picture to 3D (`headless_meshgen.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_meshgen.py
```

Socket port **9890**. Needs no GPU, no models and nothing on 8902 or 8765: meshgen and the
geometry service are two stdlib HTTP servers on ephemeral ports, and the `.glb` under test
is one the suite exports from a cube seconds earlier — a real glTF round trip through
Blender's own exporter and importer. A real generation is a manual gate costing five minutes
of graphics card, not a test. 69 checks covering:

- **`import_generated`** — the round trip, that repair is the DEFAULT (and `repair: false`
  says so in the result), that the adaptive voxel size lands in range and an explicit one
  overrides it, that the counts from before the repair survive for the report, that a 20 mm
  cube comes back 20 mm and that the glTF scene graph is collapsed to one parentless object;
- **the two refusals** — an `.stl` points at Import Model instead, a missing file says so;
- **Generate 3D from Picture** — the whole job against the fake service: the picture posted
  as an absolute path, the job followed through every state rather than slept through, the
  file it names imported and repaired, and the status line in minutes;
- **which picture it uses** — this box's field first, the Assistant box's attachment
  otherwise, and a plain "attach a picture" when there is neither;
- **the health dot** — five rows, up / models-missing (a warning naming the file to
  download) / not running (with *Start services* in the sentence), and the rule that only
  this row being down is **not** an error;
- **the panels** — drawn for real against the recording layout, with the stage line and its
  per-stage caveat on screen while a job runs.

### The UI batch (`headless_ui_batch.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_ui_batch.py
```

Socket port **9888**. Needs nothing running: the geometry service and the assistant bridge
are replaced for the run by two stdlib HTTP servers on ephemeral ports, serving canned
`/check_mesh`, `/segment_mesh` and `/health` responses in the shapes
`docs/architecture.md` promises. 156 checks covering:

- **undo checkpoints** — read-only commands push nothing, the message is `Forge: <command>`,
  and a `load_mesh` over the socket followed by `ed.undo` really removes the object. If a
  Blender ever refuses to push, the suite reports the guard rather than failing, because the
  commands themselves must still work;
- **the health row** — four rows, both healths parsed off the fakes, a signed-out CLI shown
  as a warning that names `/login`, a dead port reported as down, the socket row read from
  this process, and the session cost riding along on the poll;
- **the chips, the queue and the footer** — the three canned sentences are asserted word for
  word against what reaches `/ask`, a reply that starts life `queued` still lands in the log,
  and `This session: $0.42` is formatted from the bridge's own total;
- **the full-reply viewer** — truncation detection and the popup operator running headless;
- **`check_model` / `segment_model`** — the request bodies are inspected: a 20 mm cube
  arrives as 6 faces reaching 10 mm from the origin (metres × 1000), the printer profile
  rides along, the joint and mode travel verbatim, the answers land in the Print Checks rows,
  and the pieces are loaded at their plate positions. Plus the refusal path: a non-watertight
  answer is surfaced with "Voxel Repair" in it, the button appears, and it rebuilds the mesh;
- **Import Model** — a real STL is exported and re-imported, and comes back 20 mm across
  rather than 20 metres;
- **the flow editor** — a copy of the starter flow is reordered and re-defaulted, nothing is
  written before Save, the typed default and the new order are read back off disk, and
  saving a one-step flow is refused with its reason;
- **the empty states** — every panel's `draw()` is *executed* against a recording layout, on
  an empty scene and again on a full one, and each box's sentence is asserted. A panel is
  the part of an add-on no headless test usually reaches, which is exactly how an
  empty-state sentence rots.

### The Assistant speed selector (`headless_model.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_model.py
```

Port **9889** (HTTP). Needs nothing running: the bridge is a small `http.server` inside the
harness that records every `/ask` body. 44 checks over: the selector registering as an enum
offering exactly `haiku`/`sonnet`/`opus` in that order, labelled *Fast* / *Smart
(recommended)* / *Deepest* with a "when to use it" description on each and no model name
anywhere an artist can see; the add-on preference declaring the same three with `sonnet` as
its default; an untouched scene reading that preference (and following it when it changes,
including a brand-new scene) while a sanity guard catches a preference value that is not a
model; a choice made in the sidebar sticking and outranking any later preference change;
the model reaching the bridge in **every** `/ask` — the Send button, a changed selection,
and a quick-action chip alike — plus the untouched case sending the preference; and the
panel's `draw()` executed against a recording layout to prove the row is there, bound to
`chat`, drawn once and label-less so it stays one compact row. What the *bridge* does with
the field is `assistant/tests/test_bridge.py`'s job.

### Phase 6c — reference images (`headless_reference.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_reference.py
```

Socket port **9886**, plus a fake assistant bridge on 9887. No geometry service, no Claude
CLI, no assets: the test images are written byte by byte with `zlib`/`struct` inside the
harness (a 40x25 PNG and a 25x40 one, so "the longer side is `size_mm`" is a claim with a
right and a wrong answer) and removed afterwards. 92 checks covering: `load_reference`
makes an EMPTY of type IMAGE; each view's rotation and its offset direction; the size maths
in both orientations; the 0.5 opacity; a second load replacing rather than duplicating; the
five refusals (missing, `.txt`, unreadable `.png`, folder, name taken by a mesh); that
`export_stl` will not take a reference; and the panel side — the `image_path` property with
its `FILE_PATH` subtype, `forge.assistant_clear_image`, the validation messages, the chip,
and the field clearing itself after a successful send.

### Previews — the assistant's eyes (`headless_preview.py`)

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup `
    --python addon\tests\headless_preview.py
```

Socket port **9891**. No geometry service, no Claude CLI, no assets: the "generated part" is
a 48-segment bowl built in millimetres inside the harness and pushed in through `load_mesh`,
which is the real PartForge path. The startup Cube and Light are removed first (the Cube
would quietly get framed alongside the part; the Light would make *"did it add one?"*
unanswerable) — the startup **Camera stays on purpose**, because it is what proves
`scene.camera` is handed back afterwards. **117 checks**, and the interesting ones are
measured on the pixels rather than asserted about the code: the PNG magic bytes and an IHDR
that says the resolution that was asked for; a lone cube parked at (5000, −4000, 3000) mm
coming out centred within 12% of frame centre and covering 5–95% of it (so the camera is
fitted to the bounds, not parked at the origin); four views that are four genuinely
different files; `objects` framing one part's bounds and two parts' bounds; and the whole
state comparison — deliberately unusual settings first (EEVEE, `//artists_own_render_path`,
1920×1080 at 50%, JPEG, transparent film, MATERIAL viewport colours, cavity off, an object
the artist had already excluded from renders) so restoring to the factory defaults would
pass a weaker test than this one. A render that *fails* is checked to restore just as
completely. The interactive guard is both a source assertion (`bpy.ops.render.render` and
not `render.opengl`; no `space_data`, no `screen.areas`, no `temp_override`) and a real
measurement — `--background` still builds one off-screen VIEW_3D, so the harness sets that
viewport to MATERIAL colours in perspective, renders, and proves its shading and view matrix
are byte-for-byte unchanged.

### Phase 6b — Flows (`headless_flows.py`)

Port **9884**. Needs no geometry service; if one happens to be listening on `--service`
(default `http://127.0.0.1:8765`) it takes one read-only `/parse_params` to prove a service
step end to end, and says so plainly when it skips it instead.

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python addon\tests\headless_flows.py -- --service http://127.0.0.1:8765
```

97 checks over: `flow_list` finding the repo's `segment-into-4` with its description,
parameters and two labelled steps; that starter flow's wiring (`/segment` with
`include_mesh: true`, then `load_meshes` reading `{{steps.0.result.segments}}`);
`flow_run` on an inline flow executing Blender steps in order with per-step reports;
`{{param}}` arriving as a *typed* value (the number 6, not `"6"`) and panel strings
coercing to it; `{{steps.N.result.field}}` carrying one step's output into the next;
fail-fast naming the step that broke, what had already run, and refusing an unknown
command / unknown endpoint / missing flow before anything happens; the additive
segment-shaped `load_meshes` placing objects at their plate positions in metres; the panel
wiring (`fl` bindings, all three operators, the parameter list with its units); the Run
button doing the thing and reporting into the status line; a broken flow file listed *with*
its error; and the Assistant's activity lines (Phase 6b's other half) turning a bridge
activity list into drawable rows.

### Phase 7 — the generator handoff (`headless_partforge_open.py`)

The only suite that wants the **live** geometry service (`--service`, default
`http://127.0.0.1:8765`) — and it uses it read-only: one `/parse_params`, which builds no
geometry and writes nothing. Everything else runs with the service pointed at a dead port
and the schema supplied inline, so the panel plumbing is proved with no service at all.

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python addon\tests\headless_partforge_open.py -- --service http://127.0.0.1:8765
```

Port 9883. 40 checks over: `partforge_open` being registered without disturbing the older
commands; the live open of `service/samples/ring_band.py` putting all five parameters on the
panel with the right widget kinds (mm → float slider with its range, bool → checkbox); the
object name following the script (and a `part.py` taking its *folder's* name); a supplied
schema needing no service; `keep_values` keeping a tuned value and its absence restoring the
default; an explicit `object` winning; stale check rows being cleared when the script
changes; and the three failure paths — no `script_path`, a file that is not there, a service
that is not running — each a plain error with the panel left exactly as it was.

### Phase 6 — the Assistant panel (`headless_assistant.py`)

Needs **no geometry service, no Claude CLI and no network beyond loopback**: the bridge
it talks to is a twenty-line `http.server` inside the harness answering `/ask`, `/job`,
`/new` and `/cancel` with canned JSON.

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python addon\tests\headless_assistant.py
```

Port 9882 (HTTP, not the command socket — the assistant does not speak that protocol).
78 checks over: registration and panel ordering; the context the panel builds (active
object in millimetres, scene summary, PartForge script path); a Send that round-trips a
canned reply into the chat log with its cost and duration; the six-exchange log cap; New
Conversation clearing both the log and the bridge session; a bridge error and a 409 both
arriving as plain-language status lines; and — character for character — the sentence
shown when nothing is listening. Whether the *bridge* builds the right command line is
`assistant/tests/test_bridge.py`'s job; the two suites never overlap.

### Phase 5 — cloth and animation (`headless_phase5.py`)

Needs **no geometry service** and **downloads nothing**. The mocap clip the retarget test
consumes is written by the test itself: a hand-authored nineteen-joint BVH with twelve
frames of rotation, put in a temp folder and deleted with it.

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python addon\tests\headless_phase5.py
```

Port 9881. It imports Phase 4's builder (which imports Phase 3's) rather than copying it,
carries the character the whole way — tag → retopo → metarig → generate → **garment →
actions → keyframes → retarget** → glTF — and parses the exported `.glb` back out. 150
checks over: the garment's measured offset from the body, its inherited weights and
armature modifier; the cotton sim settling, baking a `Settled` shape key that actually
differs from the basis, and leaving no cloth or collision modifier behind; the `-loop`
round trip through `new`/`list`/`duplicate`/`rename`/`delete`/`push_nla`; keyframe values
read back off the F-curve with the right interpolation and a near-miss bone name answered
with a suggestion; the retarget's mapping table, hip travel, scene cleanliness and purged
import; and finally an export whose `.glb` carries both garments, the `Settled` morph
target and both `-loop`-suffixed clips.

### The rigging bridge — `joints_file` and `rig_check` (`headless_rigbridge.py`)

Needs **no geometry service, no GPU and no model download**:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python "C:\...\forge\addon\tests\headless_rigbridge.py"
```

108 checks on port 9901. The count is stable run to run: the per-joint assertions
cover the eight limb joints, which are always measurable, and the spine ones (whose
vertex neighbourhoods depend on where Quadriflow landed that run) are checked in
aggregate. It imports the same synthetic tagged biped as Phase 4 (so the
three suites cannot drift apart), retopologises it, and then:

- **The bridge, against a hand-written detector file.** The detector's output is a *file*,
  so the suite writes its own: predictions derived from the tag-only fit with known
  offsets baked in. That is the point of the file handoff — the blending rules are
  testable to the millimetre with no CUDA. It checks that landmarks move **towards** the
  predictions and land **about half way** (weight 0.5, asserted within 15%), that
  `joints_weight: 0` moves nothing while still reporting, that `1` hands the landmark over
  outright, that the deliberately-wrong elbow prediction is reported as a **disagreement**
  with both positions and does not move the bone, and that landmarks merely *next to*
  another joint are not reported as conflicts.
- **The frame gate.** The same numbers relabelled `axis_up: "Y"`, and metres written into
  a millimetre field, are both refused with a warning that names the reading that would
  have worked — and the fit is asserted identical to the tag-only one. A missing file and
  an empty `joints` array are errors, not silent no-ops.
- **Best effort.** A *named* prediction places an index finger on the full human template
  and the bone is checked to have really moved; a prediction named `hand` is checked to be
  taken **once**, by the wrist landmark, by name — not a second time for the hand bone.
- **The harness.** `rig_check` on the generated rig: every knee, elbow, hip and shoulder
  measured, three poses each, a rest volume to compare against, a volume loss for every
  pose, a verdict per metric, a measured bend direction — and, per joint, that the control
  **actually moved the flesh**. Then the pose is compared bone by bone against a
  fingerprint taken before the call.
- **That it can fail.** The elbow's weights are hard-bound (every vertex snapped to one
  bone, the classic no-falloff mistake) and the harness is required to report a
  measurably different volume loss. A gate that cannot fail is not a gate.
- The knobs: a joint filter, the one-pose `quick` set, intersections switched off, an
  explicit angle list echoed back, and three error paths (unknown rig, a mesh passed as a
  rig, an unknown pose set).
- The panel button: that it exists, maps to a registered operator, is drawn disabled off
  an unrigged mesh, runs when clicked, and reports through the RigForge status line.

The **real** UniRig smoke test is deliberately *not* here — it needs a GPU, 5.4 s and
8.5 GB of VRAM per mesh. It is run by hand, once, and written down in
`C:\forge-models\unirig\FORGE-NOTES.md`.

### Phase 4 — rig and Godot export (`headless_phase4.py`)

Needs **no geometry service**; it does need Rigify, which the add-on enables itself:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python "C:\...\forge\addon\tests\headless_phase4.py"
```

It imports the synthetic sculpt from the Phase 3 suite (so the two cannot drift apart),
adds two ear caps to the head, tags all eight regions, retopologises to 5 000 faces with
two LODs, and then drives the whole of stage 4 and stage 7 over a real socket on port
9880:

- **Metarig.** Every fitted joint is checked against the *tag's own bounds* — the head
  bone starts and ends inside the Head tag, each shoulder/elbow/wrist inside its Arm tag,
  each hip/knee/ankle inside its Leg tag — plus that the limbs are not degenerate
  (Rigify's IK needs a pole), that the metarig is the sculpt's size and not Rigify's
  default human, that both ear tags became chains spanning their own tags, and that the
  chains read "floppy" out of the motion notes.
- **Generate.** Deform bones exist, the mesh is parented and skinned, tags survive, and
  the plan's own example is verified on the mesh rather than in the report: pick torso
  vertices well below the neck band and assert that **no head-bone weight survives on
  them**. Then: every vertex weighted, none over four influences, all summing to 1.
- **Export.** The `.glb` is parsed back out (the JSON chunk of the binary container) and
  checked joint by joint: the skin is there, the deform bones and the ear chain are in it,
  **no `ORG-`/`MCH-`/`WGT-` bone leaked in**, the only non-`DEF-` joint is `root`, the
  baked clip is present under its own name and animates many bones rather than the one
  that was posed. Then **tangents** — every mesh primitive in the written glTF carries a
  `TANGENT` attribute, next to the `TEXCOORD_0` it is paired with — and the **Godot LOD
  convention**: the default export invents no `-lodN` sibling, leaves the generated levels
  out entirely, says so in a warning, and writes an empty `LOD_CHAIN`. A second export
  with `lods: "manual"` then asserts the levels ship under their own names, with tangents,
  and that the import script wires `visibility_range_begin`/`_end` over a contiguous,
  strictly increasing chain keyed on the glTF's own mesh names. Then the `.gd` helper's
  contents, a root-motion export, an export refused for an unknown action name, and —
  after each — that the scene was left exactly as it was found. 179 checks.
- Finally the panel wiring, the panel operators, and the quadruped and full-human
  templates on throwaway copies.

### Phase 3 — RigForge (`headless_rigforge.py`)

Needs **no geometry service**:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
    --background --factory-startup `
    --python "C:\...\forge\addon\tests\headless_rigforge.py"
```

It builds its own sculpt in-script — six overlapping spheres subdivided to ~36 000 faces,
with a hole punched in the torso and a three-faced edge welded on, so the mesh is
genuinely non-manifold and the voxel pass has to earn its place (the suite asserts that
plain `remesh mode=quad` refuses it first). Then it drives every `rigforge_*` command over
a real socket on port 9879: tag/list/untag round trips, selection-based tagging in
face-select mode, a manifest save→load round trip checked against
`templates/character.json`'s schema, a retopo to 5000 faces with two LODs (asserting the
sculpt is untouched, the tags transferred, and the count landed within 25 % of target),
auto-UV (asserting every tag-change edge is seamed, that no seam-free path runs from the
head to the torso, that every face has UV area, and that coverage clears 0.45), a
best-effort normal bake, and the panel operators and wiring.

It also pins the **finishing order**: that the unwrap stage runs after the tag transfer
and before the LODs and before the bake; that `bake_normals` with `unwrap: false` is
refused in a sentence and `bake_normals()` refuses a never-unwrapped mesh without
inventing a UV layer on the way past; that each LOD **achieved its triangle budget** and
recorded a measured error and a strictly increasing switch distance; and that every LOD
**shares LOD0's atlas** — checked twice over, exactly for the vertices the collapse did
not move, and statistically for all of them against LOD0's interpolated UV at the nearest
surface point (90th-percentile drift under 0.02; measured 0.0016 at LOD1, 0.0058 at LOD2).
139 checks; it ends with `RESULT: OK` and frees its port.

### Phase 2 — PartForge

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
