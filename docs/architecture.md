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
| `load_mesh` | `{"name": str, "vertices": [[x,y,z],...], "faces": [[i0,i1,i2,...],...], "replace": bool, "collection": str?}` — PartForge regen path; `replace` swaps mesh data in place preserving object/panel state | `{"object": name, "vertex_count", "face_count"}` |
| `export_stl` | `{"objects": [names], "path": str, "scale": float?}` — empty/omitted `objects` = current selection, else active object; `scale` defaults to 1000.0 (scene metres → STL millimetres) | `{"path": str}` |

Units: Blender scene units are meters; geometry service works in millimeters. `load_mesh` receives **millimeter** coordinates and the add-on scales by 0.001 on import (objects keep scale 1.0; vertices are scaled).

## Geometry service HTTP API (8765)

JSON request/response bodies.

- `GET /health` → `{"status": "ok", "build123d": "<version>"}`
- `POST /parse_params` `{"script": "<source>"}` → `{"params": <PARAMS schema, resolved>}` (no geometry built)
- `POST /generate` `{"script": "<source>", "overrides": {"<param>": value, ...}}` → `{"params": <schema>, "mesh": {"vertices": [[x,y,z]mm,...], "faces": [[i,...],...]}, "stats": {"vertex_count", "face_count", "bounding_box_mm": [x,y,z], "watertight": bool}}`
- `POST /export` `{"script", "overrides", "format": "stl"|"step"|"3mf", "path": "<absolute output path>"}` → `{"path": str}`

Errors: HTTP 400 with `{"error": "<message>", "traceback": "<optional>"}` for script/param failures; 500 for service bugs.

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
