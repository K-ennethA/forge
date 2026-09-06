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
| `partforge_open` | `{"script_path", "keep_values"?, "object"?, "params"? (inline schema, skips the service — headless isolation)}` — points the PartForge panel at a script via its own sync path; clears the previous part's checks/verdict/stats | `{"script", "param_count", "params", "object", "schema_source": "service"\|"supplied"}` |

**Part-object naming convention (both sides):** a part script's Blender object is the script stem, or the folder name when the stem is generic (`part`, `main`, `model`, `script`, `build`, `generate`, `__init__`), capped at 63 bytes.

**Generator write contract:** `partforge_new_part` (MCP, tool count now 42 with `partforge_open_in_panel`) is the ONLY tool that writes source, and only `projects/<slug>/part.py` + `spec.json` (slug: lowercase, non-alphanumeric→`-`, ≤60 chars; path-shaped names refused, never sanitised; `/parse_params` validation before any write; env `FORGE_PROJECTS_DIR`). The assistant never holds Write — file creation happens inside the MCP tool; its allowlist stays `Read,Glob,Grep,mcp__forge__*`. This is what keeps generator-first safe.
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
- `forge_lib` — importable in part scripts (injected like build123d): `peg(d, l)` keyed anti-rotation peg, `socket_for(peg_params, tolerance)` matching negative; printability helpers (blunted_taper, flared_lip, textured_band, feet_ring, arcade_base, magnet_pocket, shell_box, screw_boss); ornament helpers (leaf_collar, petal_crown, scale_band, silhouette_part — organic-looking decoration as watertight parametric solids; droop sweet spot 42–48°, negative clearance unions a collar onto a base). Samples: `appendage_peg.py`, `magnet_holder.py`, `eevee_style_bowl_base.py` (hybrid function+character pattern, `part` param selects base/ear/tail).
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

Implemented refinements (additive; full detail in addon/README.md): `rigforge_metarig` + `preset` (auto picks 29-bone basic human unless face/hand tags exist); `rigforge_generate_rig` + `max_influences`/`band`/`spring_chains`; `rigforge_weights` + `rig`/`band`; `rigforge_export_godot` + `lods`/`frame_step`/`unit_scale` (`deform_only: false` warns and exports deform-only anyway; `path` is the glTF file, default `.glb`). MCP mirrors bring the tool total to 36 (40 after Phase 5); `spring_chains` derives from manifest motion notes, not an MCP param. Secondary motion v1 is a deterministic MCH lag/Damped-Track constraint rig (no state, bakes cleanly; helpers non-deform so they never export). Exporter traps encoded: `export_animation_mode="ACTIONS"` leaks other objects' actions (NLA_TRACKS used), and clip-name collisions are avoided by renaming source actions aside during export.

### Original sketch

New socket commands (additive; refinements folded back by the orchestrator). No external add-on downloads: the deform-rig conversion (Game Rig Tools' job in the plan) is implemented natively — bake control-rig animation onto deform bones, strip non-deform bones on export.

| type | params | result |
|---|---|---|
| `rigforge_metarig` | `{"object", "archetype"?: "auto"\|"biped"\|"quadruped"\|"custom", "modules"?: [{"kind": "limb"\|"spine"\|"tail"\|"chain", "tag": str}], "spring_chains"?: auto-from-motion-notes}` — places and scales a Rigify metarig from tag bounds/landmarks (head top, chin, shoulder, elbow, wrist, hip, knee, ankle by tag geometry); ear/tail tags become bone chains flagged for spring/jiggle per manifest motion notes | `{"metarig", "bone_count", "mapping": {tag: [bones]}, "warnings"}` |
| `rigforge_generate_rig` | `{"metarig"?, "mesh"?, "parent_with_weights": true, "cleanup": true}` — Rigify generate → parent mesh with automatic weights → per-tag cleanup rules (e.g. no head weights below the neck tag), normalize | `{"rig", "weighted", "cleanup_report", "warnings"}` |
| `rigforge_weights` | `{"object", "action": "report"\|"cleanup"\|"normalize", "max_influences"?: 4}` | `{"report"/"changed"}` |
| `rigforge_export_godot` | `{"rig", "meshes"?, "path", "actions"?: "all"\|[names], "root_motion"?: false, "deform_only": true, "godot_import_script": true}` — bake all actions onto deform bones, strip control bones, glTF export (Y-up, applied transforms, unit scale, `-col`/`-lod` suffixes, `-loop` action convention) + emit a Godot `.gd` import helper or `.import` settings | `{"path", "actions": [names], "deform_bones", "files"}` |

MCP mirrors as thin `rigforge_*` tools. Panel: Rig box (archetype, Place Metarig, Generate Rig, weight cleanup) + Export box (path, root motion toggle, Export to Godot).

## Phase 5 — cloth and animation — implemented

Implemented refinements (additive; full detail in addon/README.md): `rigforge_cloth` + `self_collision`/`subdivide`/`rig` (presets are Blender's own Cotton/Leather/Denim tables; boundary loop auto-pinned; sim sanity-checked, exploding results delivered un-simmed with a warning; sim modifiers always removed — only the skinned mesh + `Settled` shape key ship); `rigforge_action` `rename` also applies/strips the `-loop` suffix in place, every call returns the whole library; `rigforge_keyframe` + `loop`/`fk_switch` (quaternion controls forced to XYZ euler and reported; **`IK_FK` is switched to FK and keyframed** — without it FK poses export as T-pose); `rigforge_retarget` + `frame_step`/`replace`/`fk_switch`, maps to FK controls via an ordered fragment table (forearm before arm, etc.), bakes only mapped bones, reports unmapped with reasons. MCP: 40 tools, `frames` with `skin_tight` is an error, per-action argument roles pinned, retarget `action_name` defaults to the source filename stem.

### Original sketch

New socket commands (additive; refinements folded back by the orchestrator). No downloads: mocap retargeting operates on files the USER supplies (BVH via built-in importer, FBX via built-in importer); nothing is fetched.

| type | params | result |
|---|---|---|
| `rigforge_cloth` | `{"object" (body mesh), "tags": [names] \| "use_selection", "name"?, "offset_mm"?, "thickness_mm"?, "preset": "cotton"\|"leather"\|"heavy", "output": "skin_tight"\|"shapekeys" (v1; "bones" may warn), "frames"?: int, "collision": true}` — duplicate tagged faces, offset outward, solidify, cloth sim with preset + body collision; `skin_tight` copies the body's weights (no sim); `shapekeys` bakes the settled sim into shape key(s) | `{"garment", "output", "shape_keys"?, "warnings"}` |
| `rigforge_action` | `{"action": "new"\|"list"\|"delete"\|"duplicate"\|"rename"\|"push_nla", "name"?, "source"?, "rig"?, "loop"?: bool}` — manage the Godot action library (`idle`, `walk`, `run`, `jump`, `attack`, `-loop` suffix convention enforced when `loop` true) | `{"actions": [...]}` |
| `rigforge_keyframe` | `{"rig", "action", "keys": [{"bone", "frame", "rotation_euler_deg"?/"location"?/"scale"?}], "interpolation"?: "BEZIER"\|"LINEAR", "clear"?: bool}` — batch keyframing on control-rig bones so described-motion passes are one structured call instead of raw Python | `{"action", "keys_set", "frame_range"}` |
| `rigforge_retarget` | `{"source_path" (.bvh/.fbx, user-supplied), "target_rig", "action_name", "mapping"?: "auto"\|{src: dst}, "loop"?: bool, "scale"?: "auto"}` — import the clip, map bones by name heuristics (report unmapped), transfer rotations (+ hip location) onto the rig, bake to an action, delete the import | `{"action", "mapped", "unmapped", "frames"}` |

MCP mirrors as thin tools; described-motion keyframing itself is Claude at runtime using `rigforge_keyframe`/`rigforge_action`. Panel: Cloth box (tags/selection, preset, output, Make Garment) + Actions box (library list with loop badges, New/Push-to-NLA).

## Phase 6 sketch — Forge Assistant (AI chat inside Blender)

Product philosophy — THE governing contract for the assistant, from the user verbatim in intent: **abstract difficulty away; enable the artist without them being an expert on the tech or Blender.** Concretely, every assistant reply follows one of three shapes:
1. **Did it** — the request was achievable with tools: do it, then say what changed in plain language.
2. **90% + handoff** — do everything the tools can, then give the remaining steps as a numbered, beginner-level list naming exact UI locations ("press N → Forge tab → Segments box → set Radial to 4") for the part only a human hand/eye can do.
3. **Can't do it, here's how you do it** — when tools can't help (sculpt detail, artistic judgment, GUI-only work): a step-by-step beginner walkthrough of the commands/approach, jargon explained inline, no assumed Blender knowledge.
Manual panels always remain; the assistant is a layer, never a replacement.

Component `assistant/` — a thin local bridge, stdlib-only Python (no venv needed), HTTP on `127.0.0.1:8901`:
- `GET /health` → `{"status", "claude_cli": {found, version}}`
- `POST /ask` `{"message": str, "context": {"active_object"?, "objects"?, "script_path"?}, "conversation": "continue"|"new"}` → starts a job; `{"job_id"}`
- `GET /job/<id>` → `{"state": "running"|"done"|"error", "reply"?, "actions"?: [summarized tool calls], "error"?}` (panel polls)
- `POST /cancel/<id>`
The bridge spawns the Claude Code CLI headless in the forge repo (user's existing subscription auth; exact flags per the CLI-facts findings) with only the Forge MCP tools + read-only repo access allowed, injects the context + the philosophy above as system-prompt append, and keeps one conversation per Blender session via CLI session continuity. Add-on panel: Assistant box (message field, Send, chat log of the last exchanges, busy state, New Conversation), urllib + background thread + timer polling per the existing PartForge async pattern.

### Phase 7 — meshgen (image-to-3D, swappable backends) — implemented

Implemented (port **8902**; ComfyUI child on 8188, lazy-started, windowless, process-tree-killed on stop): backends `trellis2` (default) + `pixal3d`, both via ComfyUI **core** nodes v0.34.0 (MIT-clean chain; no custom node packs — they reintroduce non-commercial nvdiffrast/RMBG). Weights at `C:\forge-models\` (18.45 GB total incl. ComfyUI+cu130 torch on Python 3.14; sha256 ledger at downloads\sha256.txt; relocatable via meshgen\config.json). `POST /generate3d` → 202 `{job_id (= ComfyUI prompt_id), state, backend, output}`; `/job` carries per-stage `progress` (resets per node — not overall), `stats`, `vram`; one job at a time, extras queue. `VRAM_SAFE_DEFAULTS` (shape 1024/tex 512/2048/200k) prevent the stock-template 20 GiB OOM on 12 GB cards; callers can override upward. `/health` free + names any missing weight file with path and URL. Proven E2E on RTX 5070: trellis2 304 s / 8.15 GB peak; pixal3d 249 s / 9.30 GB; output correctly diagnosed by /check_mesh (raw AI meshes are non-manifold with paper walls — voxel repair is the mandatory next step). Corrections vs research: ComfyUI moved to Comfy-Org/ComfyUI; DINOv3 file lives in the Pixal3D HF repo; cu130 (not cu128) required; MoGe is pixal3d-only.

### Original sketch

New component `meshgen/` — a fourth localhost service wrapping image-to-3D AI models behind ONE stable API so the model is a plug, not a dependency:
- `GET /health` → `{"status", "backend": {"name", "model", "license", "loaded": bool, "vram_gb"}, "available_backends": [...]}`
- `POST /generate3d` `{"image_path", "options"?: {backend-specific, passed through}, "output"?: path}` → async job (`{"job_id"}`) + `GET /job/<id>` (queued/running/done/error, progress if the backend reports it) → result `{"mesh_path": <.glb or .obj>, "stats": {verts, faces}, "backend", "duration_ms"}`.
- Backend adapter interface (`meshgen/backends/<name>.py`): `info()`, `ensure_ready()` (downloads/loads weights lazily, reports what it will fetch BEFORE fetching), `generate(image_path, options, out_path)`. Selected via `FORGE_MESHGEN_BACKEND` env / config file; swapping = one config line, adapters own their own venv/requirements so heavyweight deps never leak into the other services.
- Downstream is the EXISTING pipeline: output mesh → Blender import (Model box / load path) → voxel repair → /check_mesh → retopo/tag (RigForge) or /segment_mesh (printing). Meshgen generates; Forge finishes.
- Model choice policy: prefer permissive licenses (MIT/Apache) for commercial-clean output; weights safetensors-only from official publisher accounts, pinned versions; fully offline after download. Hardware budget: RTX 5070 (12 GB VRAM), 32 GB RAM, Windows.

### Phase 6d — mesh input ("fix this downloaded model") — service implemented

- `POST /check_mesh` `{"mesh": {vertices,faces} | "file_path": <abs .stl/.3mf/.obj>, "printer"?, check options, "weld_tolerance_mm"?}` → standard checks envelope (+`mesh_input` block, `params: null`, `solid_is_valid: null`). **Never refuses a broken mesh** — diagnosis always answers; an open mesh just fails `watertight`. No triangle ceiling.
- `POST /segment_mesh` (+ `/export_segments_mesh`, default basename `model`) — same input + the /segment options + `sew_tolerance_mm`/`tri_limit`. Welds (proximity weld — binary STL float32 corners never match exactly), repairs inside-out winding via signed volume (`winding_flipped`), sews watertight meshes into an OCC solid and reuses the segmenting machinery wholesale. Non-watertight → 400 with the pinned beginner message (`mesh_input.REPAIR_FIRST_MESSAGE`). **Triangle ceiling 60 000** (measured: 150k would exceed the 300 s segment budget; 60k ≈ 200 s worst case), env `FORGE_MESH_TRI_LIMIT` (0 disables) + per-request `tri_limit`.
- Knobs: env `FORGE_MESH_WELD_TOLERANCE` / `FORGE_MESH_SEW_TOLERANCE`; sew tolerance defaults to the scale-relative weld tolerance (bbox·1e-6, clamped ≤1e-3 mm). 3MF units honoured (metres→mm); build-item transforms not applied (one instance per mesh); `.step` input refused with a pointer to /check. Multi-shell sewing: largest shell = skin, rest = voids — two separate objects in one file must be separated in Blender first (documented limit).
- Addon/MCP half — implemented: socket commands `check_model {object?, printer?}` (→ /check_mesh, results land in the Print Checks panel props) and `segment_model {object?, printer?, joint?, mode?, collection?}` (→ /segment_mesh → load_meshes+plate); both legal flow steps; MCP mirrors bring the total to **48 tools**. Import Model imports STL/OBJ at mm scale; Voxel Repair routes through `remesh` (own undo step); `evaluated_mesh_mm()` extracts modifier-applied world-space mm meshes (500k-face ceiling).

### UI batch — implemented

Undo: every state-changing socket command pushes `Forge: <command>` (`READ_ONLY_COMMANDS` skip; works headless on 5.0.1, guarded if a future build refuses); panel "Revert last AI action". Health row panel above Assistant (service/bridge/socket/sign-in dots; Start services shells start_forge.ps1 hidden; sign-in reflects the LAST job — honest but lagging). Quick-action chips share the single send path. Seven empty-state sentences. Bridge queue: `/ask` while busy queues ONE message (`state: "queued"`, 409 only when one already waits); `/job` + `/health` gain `session_cost_usd`, `/health` gains `queued` + `last_auth_error`. Full-reply viewer dialog. Flow editor: param defaults + reorder/delete + Save (≥2 steps enforced).

### Phase 6c sketch — image references (pulled forward from v2)

- Panel: an attach-image field on the Assistant box (FILE_PATH property → Blender's file browser), shown as a chip with a clear button; the path travels in `context.image_path` on `/ask`.
- Bridge: when `context.image_path` is present, append an "Attached reference image: <path> — view it with the Read tool before answering" line to the message body. No allowlist change (Read already permitted; Claude Code's Read tool renders images).
- New socket command `load_reference` `{"path", "view": "front"|"side"|"top", "size_mm"?, "name"?, "offset_mm"?, "collection"?}` → image empty facing the viewer of that orthographic view (front faces −Y-viewer, side +X-viewer, top +Z-viewer), offset 1 mm away from the viewer; `size_mm` = the picture's longer side (default 200, aspect preserved); same name replaces. Returns `{"object", "width_mm", "height_mm", ...}`. MCP mirror `load_reference` (46 tools); accepted as a flow step op.
- System prompt: reference-image law — extract proportions/features/style intent into parameters, NEVER trace pixels (geometry is always parametric, per the plan); when the artist gives a real-world dimension, anchor the extracted proportions to it; offer to `load_reference` the image so they can eyeball the model against it.

### Phase 6b — live activity + repeatable flows — implemented

Implemented refinements (additive): `load_meshes` also accepts `/segment`'s own segment objects plus a top-level `plate` (placing by name) — what lets a flow chain `/segment` → Blender with one dotted reference; service-step arg sugar `script_path`/`printer_path` read files from disk into `script`/`printer` (blank = the panel's current script / the printer preference); step references `{{steps.N.result.<dotted.path>}}` (lookup only, list indices ok, whole-value placeholders keep their type); `steps[].args.timeout_s` per-step override; flows never nest (`flow_run`/`flow_list` refused as ops) and single-step flows are refused by `flow_save`; MCP totals 45 tools; env/prefs `forge_flows_dir`/`FORGE_FLOWS_DIR`, `FORGE_FLOW_RUN_TIMEOUT` (900), `FORGE_ASSISTANT_TEXT_INTERVAL` (2.0). Starter flow: `flows/segment-into-4.json`.

### Original sketch

**Activity streaming (visibility).** The bridge switches the CLI to `--output-format stream-json --verbose --include-partial-messages` and parses NDJSON as it arrives. Each job gains `"activity": [{"t": epoch, "kind": "tool"|"text"|"status", "label": str}]` — tool events labeled with the tool name plus a ≤60-char arg summary ("partforge_check: part.py"), text progress as occasional "thinking/writing" status markers, final reply from the result event. `GET /job/<id>` returns the growing list; the panel renders the last few lines live under the busy indicator so the artist always sees what the AI is doing. The full activity list is kept on the finished job.

**Flows (repeatability).** A flow is a saved, parameterized sequence of Forge operations that replays deterministically — no AI in the loop. Stored as git-versioned JSON in `flows/*.json`:
`{"name", "description", "params": {name: {"value", "unit"?, "description"?}}, "steps": [{"kind": "blender"|"service", "op": <socket command | endpoint>, "args": {... with "{{param}}" placeholders ...}, "label"?}]}`
- Add-on socket commands (additive): `flow_list` → flows with descriptions/params; `flow_run {"name"|"flow": <inline object>, "params": {overrides}}` — blender steps dispatch in-process via the registry, service steps via urllib; linear, fail-fast, per-step results in the response. Flows dir = `<repo>/flows` via an add-on preference.
- MCP tools: `flow_list`, `flow_run`, `flow_save(name, description, params, steps)` — `flow_save` validates ops against the known command/endpoint set and writes ONLY under `flows/` (same scoped-write philosophy as partforge_new_part).
- Panel: a Flows box — saved flows listed with Run buttons and param fields.
- System-prompt law: before improvising a multi-step job, check `flow_list` for a match and prefer running the flow; after completing a repeatable multi-step task, offer to save it as a flow (and name the params).

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
