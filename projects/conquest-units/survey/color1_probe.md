# COLOR_1 probe — Godot 4.6 glTF import

**VERDICT: COLOR_1 DROPPED by stock Godot 4.6 import (no import option keeps it). It SURVIVES AS `ARRAY_CUSTOM0` (RGBA_FLOAT, shader `CUSTOM0`) only with a small GLTFDocumentExtension (measured, 0/24 mismatches). Choose one: ship the extension as an addon in the game, or fall back to baked emissive.**

- Godot: `4.6.stable.official.89cea1439` (`C:/Users/kenne/Downloads/Godot_v4.6-stable_win64.exe/Godot_v4.6-stable_win64_console.exe`, the path Conquest's tools reference). Headless only.
- Blender: 5.0, `--background --factory-startup`.
- Artifacts: `survey/color1_probe/` (`make_glb.py`, `dump_glb.py`, `probe_project/{probe.glb, dump.gd, addons/color1/*, dump_baseline.txt, dump_with_extension.txt}`).

## 1. Exporter (Blender 5.0 glTF): emits COLOR_1 if set up correctly

Cube with two POINT-domain FLOAT_COLOR attributes: `Col0` = (1,0,0,1) on every vertex; `Glow` = (vertex_index/7, 0.25, 0.75, 1).
Export kwargs: `export_vertex_color='ACTIVE'`, `export_all_vertex_colors=True`, `export_active_vertex_color_when_no_material=True`, `export_attributes=True`.
Caveat: `export_all_vertex_colors=True` is what pulls in the non-active set. The forge exporter must set it. Without it, the glb carries only the active set (expected from the option name; not separately measured).

glb accessor list (parsed from the JSON chunk):
```
mesh ProbeMesh prim 0 attributes: {'POSITION': 0, 'NORMAL': 1, 'COLOR_0': 2, 'COLOR_1': 3}
  accessor 0 POSITION 5126 VEC3 count 24 normalized None
  accessor 1 NORMAL 5126 VEC3 count 24 normalized None
  accessor 2 COLOR_0 5123 VEC4 count 24 normalized True
   values: [(1.0, 0.0, 0.0, 1.0)]
  accessor 3 COLOR_1 5123 VEC4 count 24 normalized True
   values: [(0.0, 0.25, 0.75, 1.0), (0.143, ...), (0.286, ...), (0.429, ...), (0.571, ...), (0.714, ...), (0.857, ...), (1.0, 0.25, 0.75, 1.0)]
```
The exporter writes colors as normalized u16.

## 2. Stock Godot import: COLOR_0 goes to ARRAY_COLOR, COLOR_1 is gone

Same result on the editor-import path (`--import`, then `load("res://probe.glb")`) and on the runtime `GLTFDocument.append_from_file` path. Verbatim (`dump_baseline.txt`):
```
== IMPORTED: mesh class=ArrayMesh surfaces=1
  surface 0 format flags: VERTEX, NORMAL, TANGENT, COLOR, INDEX
  ARRAY_VERTEX: PackedVector3Array size=24
  ARRAY_NORMAL: PackedVector3Array size=24
  ARRAY_TANGENT: PackedFloat32Array size=96
  ARRAY_COLOR: PackedColorArray size=24
    unique values: ["(1.0, 0.0, 0.0, 1.0)"]
  ARRAY_TEX_UV: null
  ARRAY_TEX_UV2: null
  ARRAY_CUSTOM0: null
  ARRAY_CUSTOM1: null
  ARRAY_CUSTOM2: null
  ARRAY_CUSTOM3: null
  ARRAY_BONES: null
  ARRAY_WEIGHTS: null
  ARRAY_INDEX: PackedInt32Array size=36
  blend shapes: 0
```
(The RUNTIME_IMPORTERMESH and RUNTIME_SCENE dumps match this exactly.) ARRAY_COLOR holds COLOR_0 (all red). No array holds the Glow gradient.
`probe.glb.import` has no color or custom-attribute option (`meshes/*`, `gltf/naming_version`, `gltf/embedded_image_handling` only).

## 3. Bounded attempt: GLTFDocumentExtension, COLOR_1 into CUSTOM0, WORKS

`probe_project/addons/color1/color1_ext.gd` (about 60 lines). In `_import_post_parse` it decodes the COLOR_1 accessor from `state.json` and `state.buffers`, then re-adds each ImporterMesh surface with `ARRAY_CUSTOM0` = RGBA floats and format `ARRAY_CUSTOM_RGBA_FLOAT << ARRAY_FORMAT_CUSTOM0_SHIFT`. It is registered by an EditorPlugin for editor import, and with `GLTFDocument.register_gltf_document_extension` for the runtime path. Verbatim (`dump_with_extension.txt`), identical on IMPORTED, RUNTIME_IMPORTERMESH and RUNTIME_SCENE:
```
Color1Ext: COLOR_1 -> CUSTOM0 on mesh 0 surface 0 (24 verts)
== IMPORTED:Probe mesh class=ArrayMesh surfaces=1
  surface 0 format flags: VERTEX, NORMAL, TANGENT, COLOR, CUSTOM0, INDEX, CUSTOM0_type=7
  ARRAY_COLOR: PackedColorArray size=24
    unique values: ["(1.0, 0.0, 0.0, 1.0)"]
  ARRAY_CUSTOM0: PackedFloat32Array size=96
    unique values: ["0.0", "0.14285495877266", "0.25000381469727", "0.28570991754532", "0.42856487631798", "0.5714350938797", "0.71429008245468", "0.74999618530273", "0.85714501142502", "1.0"]
    CUSTOM0.r == blender_vertex_index/7 by position: mismatches=0/24
  (all other ARRAY_* as baseline)
```
`CUSTOM0_type=7` = `Mesh.ARRAY_CUSTOM_RGBA_FLOAT`. The per-vertex check maps each Godot position back to its Blender vertex index (glTF Y-up: Blender (x,y,z) becomes (x,z,-y)). Every R value matches. The values survived the full editor import pipeline (`ensure_tangents`, `generate_lods`, shadow meshes on).

Shader access: in a spatial `ShaderMaterial` `vertex()` function, read `CUSTOM0` (vec4, rgba = COLOR_1 rgba). Pass it to `fragment()` through a varying. `COLOR` is still COLOR_0. This comes from the Godot API mapping (RGBA_FLOAT custom channel into the CUSTOM0 vec4 built-in). A shader was not compiled or rendered, because the headless dummy renderer can't verify that.

## Caveats for the delivery-format decision
- The extension assumes glTF primitive order equals ImporterMesh surface order. That holds here (1:1). Multi-primitive meshes were not tested.
- Skinned meshes and blend shapes were not tested. The extension carries blend-shape arrays through, but adds no COLOR_1 to the morph targets.
- The game needs the addon enabled in its project, an edit inside Conquest. It is a one-file GLTFDocumentExtension plus a 10-line plugin.
- Alternative route, not measured: TEXCOORD_2+ map natively to CUSTOM0..3 in Godot's importer. A glow mask exported as a third UV map would need no extension, but only gets 2 channels.
