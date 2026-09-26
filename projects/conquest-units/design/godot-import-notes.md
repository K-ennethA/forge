# Godot import queue — deferred until models are finished (artist, 2026-09-25)

Artist directive: "lets finish models before we import into godot." This file
collects every known import-wave issue so the ship-path lane starts from a
list, not from rediscovery. Append as found; resolve when the import wave runs.

## 1. Glow mask cannot ride glTF emission (affects Geode, Vineweave, Mortis)

The palette system stores per-vertex glow in the COLOR_1 vertex-color channel
(glow tiers: seams, hollows, eyes, core). glTF has no way to wire a vertex
color to emission strength — the exporter can only set a uniform
emissiveFactor, which makes a stock importer glow the entire unit (or nothing).

Options, decide at import time:
- **Conquest-side shader (recommended):** a small Godot shader that reads
  COLOR_1 and multiplies it into emission. Cheapest, keeps palette swaps free
  (repaint = re-export, no texture bake), one shader shared by all glowing
  units.
- **Baked emissive texture at export:** add a bake step to the forge export
  path producing an emissive PNG per unit/skin. Works in any stock consumer,
  but every palette swap then needs a re-bake, and texture memory grows per
  skin.

Probe evidence: `improve/geode_gltf_probe.py` → `rigged/geode_gltf_probe.json`.

## 2. Idle emission-pulse keys export only via KHR_animation_pointer

Geode's idle keys emission strength for the core pulse. Blender's glTF
exporter emits those keys only with KHR_animation_pointer in scene-animation
mode; Godot's importer support for that extension needs verifying. Fallback:
drive the pulse in the Godot shader (time-based), drop the baked keys.

## 3. Per-skin variant delivery shape is undecided

Skins currently ship as separate variant .blends (`rigged/<unit>__<skin>.blend`).
For Godot the cheaper shape is probably ONE mesh + per-skin material overrides
(palette repaint is pure vertex color, geometry identical). Decide: N glbs vs
1 glb + N material resources. Interacts with item 1 (shader path makes
material-override skins nearly free).

## 4. Scale at import (natural-scale policy)

Units are authored at natural proportions; cell_fit is report-only during
authoring. The game scales at import — the import wave must pick and pin the
per-unit scale factors (assets.conf allowances are the reference; eldroot's
was already raised 3.4 → 5.6 with artist authorization).

## 5. Translucent units (magmoo v3): alpha blend + sorting

The glb material is alphaMode BLEND, single-sided; per-region alpha rides
COLOR_0.a (VEC4) x baseColorFactor[3]. The Conquest glow shader (item 1)
must multiply COLOR.a into ALPHA and use depth_prepass_alpha (or
StandardMaterial3D transparency = Alpha Depth Pre-Pass) with cull_back.
Without the prepass, Godot sorts per mesh instance only; magmoo's 5 skinned
meshes interpenetrate at the joins (goo into goo) and triangles draw in
index order, so the inner plugs show and flicker. With it, only the front
goo surface blends over the world. Evidence:
renders/magmoo/magmoo_v3_sorting_modes.png + magmoo_v3_translucency.json.

## 6. Cel-shaded units (vampwarrior v2): toon shader + outline shells

Toon lighting on the unit material: the asset bakes AO tone bands into
COLOR_0 and ships roughness 1 / no specular. Game side adds a ramped
light: snap N.L from the main light to 3 constant steps (~0.58 / 0.84 /
1.0 at thresholds 0.30 / 0.80) and multiply by COLOR_0; emission from
COLOR_1 (item 1). Normals are flat per the contract, so band edges follow
triangle edges - accepted as the low-poly toon look. One shader shared by
every cel unit.

Outline shells: vampwarrior_outline + vampwarrior_sword_outline are
inverted hulls (faces point inward, not double-sided) - make their
material unshaded, albedo from COLOR_0 (palette 'outline' region), cast
shadow off; hide/swap the sword outline with the sword node; hiding both
= a quality toggle. Shells are world-space 5 mm (2.5 mm face): ~1/3 px at
the 256 px tactical view. For a constant screen-width line, extrude
further in the outline shader by pixel-width x view distance (small - the
flat shading cracks at corners), or use the zero-triangle alternative: a
next_pass material on the body with cull_front + grow; if the game goes
that way the forge stops exporting the shells and returns ~6.4k tris to
the model. glb lists KHR_materials_specular (specular 0); Godot may
ignore it - roughness 1 already carries the flat look.
