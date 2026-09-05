# Forge — Architecture Contract

This file is the binding contract between the three components. Any change to a wire format or command name happens HERE first, then in code.

## Components and ports

| Component | Location | Runs | Listens |
|---|---|---|---|
| Blender add-on (`forge`) | `addon/forge/` | inside Blender | TCP `127.0.0.1:9876` |
| Geometry service | `service/` | standalone Python (Build123d) | HTTP `127.0.0.1:8765` |
| MCP server | `mcp/` | spawned by Claude Code (stdio MCP) | — (client of both) |

Data flow:

- Claude Code → MCP server → Blender socket (scene ops, mesh loading, common ops like symmetrize).
- Claude Code → MCP server → geometry service (generate/export parametric parts).
- Blender panel sliders → geometry service directly (HTTP via urllib, no third-party deps inside Blender) → mesh reloaded in place.

## Blender socket protocol (TCP 9876)

Newline-delimited JSON, UTF-8. One request object per line, one response object per line.

Request: `{"id": "<optional>", "type": "<command>", "params": { ... }}`
Response: `{"id": "<echoed>", "status": "success" | "error", "result": <object|null>, "message": "<human-readable, set on error>"}`

The socket server runs on a background thread; **all bpy work executes on the main thread via a `bpy.app.timers` command queue**. Errors must be caught and returned as `status: "error"`, never crash the server.

### Commands

All object-targeting commands take `"object"` (name, string); omitted = active object.

| type | params | result |
|---|---|---|
| `ping` | `{}` | `{"pong": true, "blender_version": "..."} ` |
| `get_scene_info` | `{}` | `{"objects": [{"name","type","location":[x,y,z],"dimensions":[x,y,z],"vertex_count","modifiers":[names]}], "active": "<name or null>"}` |
| `execute_python` | `{"code": str}` | `{"output": "<captured stdout>", "result": "<repr of last expr or null>"}` |
| `symmetrize` | `{"object", "direction": "+X"\|"-X"\|"+Y"\|"-Y"\|"+Z"\|"-Z"}` (direction = the side that survives is the source; maps to Blender's `POSITIVE_X` etc.) | `{}` |
| `mirror` | `{"object", "axis": "X"\|"Y"\|"Z", "use_clip": bool, "apply": bool}` — adds (and optionally applies) a Mirror modifier | `{}` |
| `remesh` | `{"object", "mode": "voxel"\|"quad", "voxel_size": float (voxel, in scene metres, default 0.01), "target_faces": int (quad, default 5000)}` | `{"vertex_count": int, "face_count": int}` |
| `decimate` | `{"object", "ratio": float}` | `{"face_count": int}` |
| `shade` | `{"object", "mode": "smooth"\|"flat"\|"auto", "angle": float_degrees (auto)}` | `{}` |
| `apply_transforms` | `{"object", "location": bool, "rotation": bool, "scale": bool}` | `{}` |
| `set_origin` | `{"object", "type": "geometry"\|"bottom"\|"cursor"}` | `{}` |
| `boolean` | `{"object", "operand": str, "operation": "UNION"\|"DIFFERENCE"\|"INTERSECT", "apply": bool, "delete_operand": bool}` | `{}` |
| `merge_by_distance` | `{"object", "distance": float}` | `{"removed": int}` |
| `separate_loose` | `{"object"}` | `{"objects": [names]}` |
| `select_object` | `{"name"}` | `{}` |
| `rename_object` | `{"name", "new_name"}` | `{"name": "<final>"}` |
| `delete_object` | `{"name"}` | `{}` |
| `load_mesh` | `{"name": str, "vertices": [[x,y,z],...], "faces": [[i0,i1,i2,...],...], "replace": bool, "collection": str?, "plate": {"position_mm":[x,y,z], "pre_rotate_deg", "rotate_deg"}?}` — PartForge regen path; `replace` swaps mesh data in place preserving object/panel state. Optional `plate` (one `/segment` `plate.items` entry, forwarded verbatim) rotates the OBJECT about Z and offsets so the rotated bbox min corner lands at `position_mm`; mesh data stays in assembly coordinates | `{"object": name, "vertex_count", "face_count", "location"?, "rotation_z_deg"?}` |
| `load_meshes` | `{"meshes": [<load_mesh params>...], "replace"?: bool, "collection"?: str, "scale"?: float, "select"?: bool}` — bulk variant; top-level keys are per-entry defaults, `select` applies to the last entry, empty list is an error | `{"objects": [<load_mesh result>...], "count", "names", "scale"}` |
| `export_stl` | `{"objects": [names], "path": str, "scale": float?}` — empty/omitted `objects` = current selection, else active object; `scale` defaults to 1000.0 (scene metres → STL millimetres) | `{"path": str}` |

Units: Blender scene units are meters; geometry service works in millimeters. `load_mesh` receives **millimeter** coordinates and the add-on scales by 0.001 on import (objects keep scale 1.0; vertices are scaled).

## Geometry service HTTP API (8765)

JSON request/response bodies.

- `GET /health` → `{"status": "ok", "build123d": "<version>"}`
- `POST /parse_params` `{"script": "<source>"}` → `{"params": <PARAMS schema, resolved>}` (no geometry built)
- `POST /generate` `{"script": "<source>", "overrides": {"<param>": value, ...}}` → `{"params": <schema>, "mesh": {"vertices": [[x,y,z]mm,...], "faces": [[i,...],...]}, "stats": {"vertex_count", "face_count", "bounding_box_mm": [x,y,z], "watertight": bool}}`
- `POST /export` `{"script", "overrides", "format": "stl"|"step"|"3mf", "path": "<absolute output path>"}` → `{"path": str}`

Errors: HTTP 400 with `{"error": "<message>", "traceback": "<optional>"}` for script/param failures; 500 for service bugs.

### Phase 2 endpoints (print readiness) — implemented

Full field detail lives in `service/README.md`; this is the contract summary.

- `POST /check` `{"script", "overrides", "printer": <partial printer.json, merged over a Centauri Carbon default>, "plate_margin_mm"?, "min_wall_probe_mm"?, "max_wall_samples"?, "tolerance"?, "angular_tolerance"?}` → `{"overall": "pass"|"warn"|"fail", "checks": [{"name": "bed_fit"|"min_wall"|"overhangs"|"watertight", "status", "details", "data"}], "printer": <resolved>, "params", "stats", "timings"}`. `bed_fit.data.suggested_segmentation.mode` is directly valid as `/segment`'s `mode`. `overhangs` reports the 6 axis-aligned orientations with `best_orientation` (warn-only). `min_wall` is inward ray casting — approximate, limits documented in service/README.md.
- `POST /segment` `{"script", "overrides", "printer", "joint": {"type": "dovetail"|"pin"|"magnet"|"none", "tolerance"?}, "mode": "auto"|{"radial": N, "start_angle_deg"?}|{"planar": [z_mm,...]}, "include_mesh"?, "plate_margin_mm"?, "plate_spacing_mm"?}` → `{"mode": <resolved>, "joint", "cuts", "segments": [{"name", "kind": "segment"|"hardware", "stats", "orient_deg", "oriented_bbox_mm", "mesh"?}], "plate", ...}`. Printed pins arrive as `kind: "hardware"` segments. Every segment is re-verified watertight; non-manifold output is a 400, not a warning.
- `POST /export_segments` — the `/segment` body plus `{"directory", "basename"?, "format"?}` → one STL per segment (oriented, centred, on Z=0) plus `<basename>_plate.3mf` packed to the bed.
- `forge_lib` — importable in part scripts (injected like build123d): `peg(d, l)` keyed anti-rotation peg, `socket_for(peg_params, tolerance)` matching negative. Sample: `service/samples/appendage_peg.py`.
- Joint tolerances come from printer.json (`press_fit` for dovetail/pin, `magnet_pocket_extra` for magnets) unless overridden. Known limitation: N radial dovetails all face the same way, so the final joint must spring; use pins or magnets for full rings.
- Timeouts: `/check` 120 s, `/segment`/`/export_segments` 300 s (`FORGE_CHECK_TIMEOUT` / `FORGE_SEGMENT_TIMEOUT`).
- `POST /slice` `{"input": <abs stl/3mf/step/obj>, "output": <abs gcode/3mf>, "profile"?, "filaments"?, "printer"?, "slicer_path"?, "extra_args"?, "timeout_s"?}` → argv, tails, duration, size. OrcaSlicer CLI verified against the real 2.3.2 binary ("C:\Program Files\OrcaSlicer\orca-slicer.exe"); flags are data-driven in `service/slicer.py`. `GET /health` reports slicer detection. Structured 400 with probe list when no slicer found.
- `POST /mold` `{"script", "overrides", "parting_z_mm": float|"auto", "draft_deg"=2, "shell_mm"=4, "clearance_mm", "spout"|false, "vents": int|"auto", "registration_keys"=4, ...}` → two watertight halves (`mold_top`/`mold_bottom`) with draft (OCC DraftAngle, taper-union fallback, per-half report), registration keys, spout, vents; `POST /export_mold` writes them. Full detail in service/README.md.

## Phase 3 — RigForge foundation (tag panel, manifest, retopo, auto-UV) — implemented

Blender socket commands (full detail in addon/README.md). Tags are vertex groups prefixed `tag_` on the object, mirrored to a `character.json` manifest (templates/character.json). **Wire convention: bare tag names** (`"Head"`); the `tag_` prefix is add-on-internal, and prefixed input is accepted and stripped on both sides. `rigforge_list_tags` entries are `{name, vertex_group, vertex_count, face_count}`.

Implemented refinements (additive): `rigforge_untag` + `include_shared` (default false — shared border verts survive); `rigforge_retopo` `target_faces` optional (explicit → manifest → platform preset), + `bake_path`/`voxel_size`, `face_counts` is a dict keyed by object name, `keep_original: false` is a warning not an error; `rigforge_manifest` + `name`/`create_missing_tags`; `rigforge_auto_uv` + `method`; `rigforge_status` also exists as a socket command (MCP currently composes its own from get_scene_info + list_tags — both valid). `get_scene_info` objects now include `face_count` (mesh objects). Retopo pipeline: adaptive voxel remesh → Quadriflow (decimate fallback, self-reporting) → applied shrinkwrap → kd-tree majority-vote tag transfer → optional Cycles normal bake (works headless; best-effort with `baked.ok`) → LOD1/LOD2 (decimate ratios are triangle budgets, so LOD1 ≈ 70% of polygons, documented). Auto-UV: seams at tag-change edges + boundaries, 66° angle fallback, angle-based unwrap, 0-1 tile pack.

| type | params | result |
|---|---|---|
| `rigforge_list_tags` | `{"object"}` | `{"tags": [{"name", "vertex_count", "face_count"}]}` |
| `rigforge_tag` | `{"object", "tag", "faces": [indices] \| "use_selection": true, "replace"?: bool}` | `{"tag", "vertex_count"}` |
| `rigforge_untag` | `{"object", "tag", "faces"?/"use_selection"? (omit = remove tag entirely)}` | `{}` |
| `rigforge_manifest` | `{"object", "action": "save"\|"load"\|"get", "path"?, "archetype"?, "motion_notes"?}` | `{"manifest": <character.json content>}` |
| `rigforge_retopo` | `{"object", "target_faces", "platform"?: "desktop"\|"mobile", "lods"?: int, "bake_normals"?: bool, "bake_resolution"?: int, "keep_original": true}` — voxel remesh → Quadriflow → shrinkwrap → tag transfer by proximity → optional normal bake high→low → optional decimated LODs | `{"objects": [names], "face_counts", "baked"?: image/path info}` |
| `rigforge_auto_uv` | `{"object", "seams_from_tags"?: true, "margin"?: float, "angle_limit"?}` — seams at tag boundaries (neck, shoulders, wrists per plan), unwrap, pack | `{"islands": int, "uv_coverage": float}` |

MCP mirrors these as `rigforge_*` tools plus a `rigforge_status(object)` overview. Panel: RigForge N-panel section — tag list with assign/remove from selection, archetype dropdown, motion-notes text, retopo settings + Run, UV Run.

## Phase 4 — rig and Godot export — implemented

Implemented refinements (additive; full detail in addon/README.md): `rigforge_metarig` + `preset` (auto picks 29-bone basic human unless face/hand tags exist); `rigforge_generate_rig` + `max_influences`/`band`/`spring_chains`; `rigforge_weights` + `rig`/`band`; `rigforge_export_godot` + `lods`/`frame_step`/`unit_scale` (`deform_only: false` warns and exports deform-only anyway; `path` is the glTF file, default `.glb`). MCP mirrors are 36 tools total; `spring_chains` derives from manifest motion notes, not an MCP param. Secondary motion v1 is a deterministic MCH lag/Damped-Track constraint rig (no state, bakes cleanly; helpers non-deform so they never export). Exporter traps encoded: `export_animation_mode="ACTIONS"` leaks other objects' actions (NLA_TRACKS used), and clip-name collisions are avoided by renaming source actions aside during export.

### Original sketch

New socket commands (additive; refinements folded back by the orchestrator). No external add-on downloads: the deform-rig conversion (Game Rig Tools' job in the plan) is implemented natively — bake control-rig animation onto deform bones, strip non-deform bones on export.

| type | params | result |
|---|---|---|
| `rigforge_metarig` | `{"object", "archetype"?: "auto"\|"biped"\|"quadruped"\|"custom", "modules"?: [{"kind": "limb"\|"spine"\|"tail"\|"chain", "tag": str}], "spring_chains"?: auto-from-motion-notes}` — places and scales a Rigify metarig from tag bounds/landmarks (head top, chin, shoulder, elbow, wrist, hip, knee, ankle by tag geometry); ear/tail tags become bone chains flagged for spring/jiggle per manifest motion notes | `{"metarig", "bone_count", "mapping": {tag: [bones]}, "warnings"}` |
| `rigforge_generate_rig` | `{"metarig"?, "mesh"?, "parent_with_weights": true, "cleanup": true}` — Rigify generate → parent mesh with automatic weights → per-tag cleanup rules (e.g. no head weights below the neck tag), normalize | `{"rig", "weighted", "cleanup_report", "warnings"}` |
| `rigforge_weights` | `{"object", "action": "report"\|"cleanup"\|"normalize", "max_influences"?: 4}` | `{"report"/"changed"}` |
| `rigforge_export_godot` | `{"rig", "meshes"?, "path", "actions"?: "all"\|[names], "root_motion"?: false, "deform_only": true, "godot_import_script": true}` — bake all actions onto deform bones, strip control bones, glTF export (Y-up, applied transforms, unit scale, `-col`/`-lod` suffixes, `-loop` action convention) + emit a Godot `.gd` import helper or `.import` settings | `{"path", "actions": [names], "deform_bones", "files"}` |

MCP mirrors as thin `rigforge_*` tools. Panel: Rig box (archetype, Place Metarig, Generate Rig, weight cleanup) + Export box (path, root motion toggle, Export to Godot).

## PARAMS block convention (the PartForge script contract)

Every generated script is a plain Python file with, at top level:

```python
PARAMS = {
    "bowl_diameter": {"value": 152.4, "unit": "mm", "min": 50.0, "max": 300.0,
                       "step": 1.0, "description": "Outer diameter of the bowl"},
    "feet_count":    {"value": 4, "unit": "count", "min": 3, "max": 8, "step": 1,
                       "description": "Number of feet"},
}

def build(p):
    # p is a dict of param name -> resolved value (overrides applied, validated)
    # returns a Build123d Part / Solid / Compound
    ...
```

Rules: `value` AND `unit` required (`unit` one of `mm`, `in`, `deg`, `count`, `ratio`, `bool`); `min`/`max`/`step`/`description` optional but encouraged. Param names must be valid Python identifiers. Out-of-range overrides are rejected by the service (not clamped); `step` is a UI hint only. `in` values are converted to mm before `build()` is called (build works in mm). The service validates overrides against min/max and type. The Blender panel is generated from this schema verbatim.

## spec.json / printer.json / character.json

Templates live in `templates/`. Each PartForge project folder under `projects/<name>/` holds `spec.json`, the generated `part.py` script, `exports/`, and renders.

## Ground rules for all components

- Windows is the primary platform; paths must handle spaces and backslashes.
- Blender add-on: zero third-party dependencies (stdlib + bpy only).
- Service and MCP server: dependencies declared in `pyproject.toml`, installed later into local `.venv`s (not during initial build).
- Nothing auto-launches Blender or opens windows. Headless verification only, and only when testing is requested.
