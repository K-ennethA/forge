# Forge — Blender add-on

The Blender half of the Forge pipeline. It does three things:

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

### RigForge commands (Phase 3)

| type | params | does |
|---|---|---|
| `rigforge_list_tags` | — | every `tag_*` vertex group with its vertex and face counts, plus how many faces are untagged |
| `rigforge_tag` | `tag`, `faces` \| `use_selection`, `replace?` | assigns the vertices of those faces to `tag_<Name>` at weight 1.0 |
| `rigforge_untag` | `tag`, `faces?` / `use_selection?`, `include_shared?` | drops faces from a tag; with no faces given, deletes the tag |
| `rigforge_manifest` | `action` `save`\|`load`\|`get`, `path?`, `archetype?`, `motion_notes?`, `name?`, `create_missing_tags?` | reads/writes `character.json` |
| `rigforge_retopo` | `target_faces?`, `platform?`, `lods?`, `bake_normals?`, `bake_resolution?`, `bake_path?`, `voxel_size?`, `keep_original` | the stage 2 pipeline; returns object names and face counts |
| `rigforge_auto_uv` | `seams_from_tags?`, `margin?`, `angle_limit?`, `method?` | seams, unwrap, pack; returns island count and UV coverage |
| `rigforge_status` | — | one-call overview: tags, archetype, motion notes, manifest path, derived meshes |

### RigForge commands (Phase 4 — rig and Godot export)

| type | params | does |
|---|---|---|
| `rigforge_metarig` | `object?`, `archetype?` `auto`\|`biped`\|`quadruped`\|`custom`, `modules?`, `spring_chains?`, **`preset?`** | builds a Rigify metarig and fits it to the tags; ear/tail tags become bone chains. Returns `metarig`, `bone_count`, `mapping` (tag → bones), `chains`, `landmarks`, `warnings` |
| `rigforge_generate_rig` | `metarig?`, `mesh?`, `parent_with_weights?`, `cleanup?`, **`max_influences?`**, **`band?`**, **`spring_chains?`** | Rigify generate → automatic weights → per-tag weight cleanup. Returns `rig`, `weighted`, `cleanup_report`, `spring_chains`, `warnings` |
| `rigforge_weights` | `object?`, `action` `report`\|`cleanup`\|`normalize`, `max_influences?`, **`rig?`**, **`band?`** | per-bone influence counts and the two numbers that mean trouble; or re-runs the rules |
| `rigforge_export_godot` | `rig?`, `meshes?`, `path`, `actions?` `all`\|`[names]`, `root_motion?`, `deform_only?`, `godot_import_script?`, **`lods?`**, **`frame_step?`**, **`unit_scale?`** | bakes every action onto the deform bones, strips the control rig, writes glTF + a Godot `.gd` import helper. Returns `path`, `actions`, `deform_bones`, `files` |

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
6. **Normal bake** (optional, `bake_normals`). High-to-low, Cycles, into a new image named
   `<obj>_retopo_normal` at `bake_resolution` (default 2048); `bake_path` also writes it
   out as a PNG.
7. **LODs** (optional, `lods`). `<obj>_lod1`, `_lod2`, … decimated at ratio 0.5, 0.25,
   0.125 …, duplicated from the retopo mesh so the tags come with them.

Returns the object names, a `face_counts` map keyed by name, the per-stage log, the
transferred tags, the bake report and any warnings.

**Bake caveat.** The bake is the one step allowed to fail. It needs Cycles, and Cycles is
a shipped add-on that a `--factory-startup` session (or a user who turned it off) leaves
disabled — so the add-on enables it for you and then lets `scene.render.engine = "CYCLES"`
be the real test, because `engine` is a dynamic enum whose static RNA item list does not
list registered engines. If any of it fails, `result.baked` comes back as
`{"ok": false, "reason": "..."}`, the reason is repeated in `warnings`, and **the rest of
the pipeline still delivers its meshes** — everything else is already on disk by then.
Cycles bakes on the CPU here at one sample; a 2048 map on a dense sculpt is not instant,
so start smaller if you are iterating. Baking also needs UVs on the low-poly mesh: if
there are none it makes a UV layer first, but you will get a better map by running the UV
pass below before the bake.

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

`RigForge ▸ Rig`. Three buttons and a weight row, in the order you use them.

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
- **`-loop`, `-col`, `-lod`.** Clip names are passed through untouched, so the `-loop`
  convention survives to the import script. Meshes suffixed `-col` / `-colonly` /
  `-convcol` are exported as-is for Godot's own collision handling and listed in the
  result. `<mesh>_lod1` / `_lod2` siblings are exported alongside, renamed to Godot's
  `-lod1` / `-lod2` (turn this off with `lods: false`).
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
  tools/partforge.py     PartForge state, HTTP client, operators
  tools/rigforge.py      RigForge tags, manifest, retopo, auto-UV, panel state + operators
  tools/rigforge_rig.py  RigForge metarig fitting, Rigify generate, weights, Godot export
  tools/rigforge_anim.py RigForge cloth, the action library, keyframing, retargeting
  ui/panels.py           sidebar panels
  blender_manifest.toml  extension metadata (Blender 4.2+ install path)
addon/tests/
  headless_phase2.py     headless checks for the Print Checks / Segments panels
  headless_rigforge.py   headless checks for the RigForge tag/manifest/retopo/UV stack
  headless_phase4.py     headless checks for the rig, the weights and the Godot export
  headless_phase5.py     headless checks for cloth, actions, keyframing and retargeting
```

`rigforge_rig.py` holds the Phase 4 commands but keeps its panel state in
`rigforge.py`'s `ForgeRigForgeProps` and reports through the same `status` line, so the
whole RigForge section behaves as one panel. `rigforge_anim.py` (Phase 5) keeps its own
`ForgeAnimProps` on the scene instead — cloth and the action library have a lot of state
and none of Phase 3's fields mean anything to them — but it reports through a `status` line
drawn the same way, so the section still reads as one panel.

Two conventions in `ui/panels.py` worth knowing before you edit it: the PartForge panels
bind their state to a local called `props`, the RigForge Phase 3/4 ones to `rf`, and the
Phase 5 ones to `ra`. They are different PropertyGroups on the scene, and the headless
panel-wiring tests tell them apart by that name. And nothing in the file does work: every
button is an operator that reports back through its panel's `status` string.

## Headless tests

Four suites, all `--background` only. Never launch Blender windowed to run them. As of
Phase 5 they are **49 + 108 + 156 + 150 = 463 checks**, all green on Blender 5.0.1.

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
  that was posed, and the LOD meshes arrived with Godot's `-lod` suffix. Then the `.gd`
  helper's contents, a root-motion export, an export refused for an unknown action name,
  and — after each — that the scene was left exactly as it was found.
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
best-effort normal bake, and the panel operators and wiring. 108 checks; it ends with
`RESULT: OK` and frees its port.

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
