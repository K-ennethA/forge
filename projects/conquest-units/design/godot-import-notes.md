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
