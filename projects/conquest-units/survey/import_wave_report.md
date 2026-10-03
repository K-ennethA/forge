# Conquest import wave: 9 roster-matched forge units (2026-10-02)

Branch `feat/forge-unit-import` in `C:\Users\kenne\OneDrive\Desktop\git\Conquest`, cut from `feat/foundation` at
**c30ce3f** (the checked-out HEAD; the audit's 232a5ab is one commit older). Nothing committed. Godot 4.6.stable, headless only.

## Integration shape: direct roster swap + import-time glow (no wrapper scenes, no code change)
The roster `.tres` points `model_scene` straight at the forge glb. Each glb's `.import` sets `nodes/root_scale` (the cell fit)
and `import_script/path` = `game/visuals/units/unit_glow_import.gd`, which adds the shared glow pass at import time.
Because of that, every consumer of `model_scene` (Unit, PortraitCache, UnitPreview3D, MapMaker, Overworld) gets the scaled,
glowing model, and `unit.gd`, SkinLibrary and UnitVisualManager stay untouched. A wrapper `.tscn` per unit would have needed
node-path overrides into nine imported scenes and would have covered the same consumers no better.

## Files
- `addons/color1/{color1_ext.gd, plugin.gd, plugin.cfg}`: `color1_ext.gd` is the measured probe extension, byte-for-byte
  (`_GLOW` first, COLOR_1 fallback, morph-name fix). It is enabled in `project.godot [editor_plugins]`, a one-entry edit
  that registering the extension for editor import requires. The runtime needs nothing: the imported `.scn` already carries CUSTOM0.
- `game/visuals/units/unit_glow.gdshader`: a spatial next_pass with `blend_add, unshaded, depth_draw_never, cull_disabled`.
  It computes `ALBEDO = CUSTOM0.rgb * glow_energy`.
- `game/visuals/units/unit_glow_import.gd`: an EditorScenePostImport. For each surface that has CUSTOM0, it turns off stock
  emission, moves the glb's emissive strength into `glow_energy`, and sets the glow ShaderMaterial as the surface material's `next_pass`.
  The surface material is still a StandardMaterial3D. `SkinLibrary.tinted_material` duplicates it and the next_pass rides along,
  so tint skins and the overlay/transparency in `UnitVisualManager` work unchanged.
- `game/characters/models/<biome>/<forge_id>_forge.glb` (9 files, identical to the copies in forge `rigged/`) plus `.import`,
  plus 8 extracted textures (`*_ao.png` / `*_normal.png` for vineweave, eldroot, mortis and duskmaw, with Godot's default
  `embedded_image_handling=1`).
  The `_forge` suffix avoids overwriting the old glbs. bastion (eldroot.glb at 0.55), oakheart (tree_grunt.glb at 1.3) and
  undead (mycothrall.glb at yaw 180) still use those old files, and those three units are out of scope.
- 9 roster `.tres` files (model path; yaw and blightcap scale where listed below), and an eldroot note line.
- `tests/unit/test_monster_kit.gd:845` (outside the stated allowlist, 1 assertion): it pinned `model_yaw_deg == 180.0` with
  the stale reason "sculpts face -Y". It now pins `0.0` with the measured reason: Blender -Y exports to +Z, which is
  model-forward (`CharacterResource.gd:73-77`, `tools/blender/README.md` "Facing").
- Not touched and still dirty: `tools/blender/assets.conf` (eldroot 5.6) and the Cinzel `.import` line-ending noise.
  **Do not stage assets.conf.**

## Applied scale / yaw (measured in the GAME project, rest AABB of the imported scene)
fit = min(1.8/h, 1.9/fp) for 1x1. eldroot (2x2) uses min(5.6/h, 3.8/fp), with the 5.6 ceiling from the artist-authorized assets.conf value.
| roster id | forge unit | glb (game) | natural h / fp (m) | root_scale (.import) | final h / fp | yaw (roster) | model_scale (roster) |
|---|---|---|---|---|---|---|---|
| vineweave | vineweave | forest/vineweave_forge.glb | 5.2711 / 4.8711 | 0.34149 (H) | 1.8000 / 1.6634 | 0 | 1.0 |
| blightcap | blightcap | forest/blightcap_forge.glb | 1.5690 / 1.9000 | 1.0 | 1.5690 / 1.9000 | **180 -> 0** | **0.4 -> 1.0** |
| petalfang | petalfang | forest/petalfang_forge.glb | 0.7602 / 1.9000 | 1.0 | same | 0 | 1.0 |
| tree_grunt | barkling | forest/barkling_forge.glb | 1.3934 / 1.9000 | 1.0 | same | 0 | 1.0 |
| mycothrall | mycothrall | forest/mycothrall_forge.glb | 0.4814 / 1.9000 | 1.0 | same | **180 -> 0** | 1.0 |
| eldroot | eldroot (standing4) | forest/eldroot_forge.glb | 4.5605 / 3.8000 | 1.0 (FP) | 4.5605 / 3.8000 | 0 | 1.0 |
| gem_knight | geode | earth/geode_forge.glb | 24.0355 / 17.8477 | 0.07489 (H) | 1.8000 / 1.3366 | 0 | 1.0 |
| necromancer | mortis | dark/mortis_forge.glb | 12.9988 / 8.0080 | 0.13847 (H) | 1.7999 / 1.1089 | 0 | 1.0 |
| monster | duskmaw (v4) | dark/duskmaw_forge.glb | 41.7073 / 28.9686 | 0.04316 (H) | 1.8001 / 1.2503 | **180 -> 0** | 1.0 |
All 9 have feet at y=0 and are centred in xz (0.0000, 0.0000). After scaling, the skin is consistent (max |rest x bind - I| <= 1.2e-5
for all 9), and the idle position-track keys equal the scaled bone rests (geode body 1.1467 = rest). `apply_root_scale` therefore
bakes the scale into the mesh, skeleton, skin and clips, and leaves the root at scale 1.

## Departures from the brief (with measured reasons)
1. **Scale lives in the glb `.import`, not in roster `model_scale`.** This follows the game's convention: `model_scale` 1.0 means
   "the pipeline-fit size" (`CharacterResource.gd:79`, README "Scale"), and the existing glbs bake the fit at export.
   `tests/integration/test_duskmaw_movement_sweep.gd:440` pins `duskmaw.model_scale == mortis.model_scale` ("no per-model scale
   override"), and roster scales of 0.0432 and 0.1385 would break it. The fit factors are the audit's numbers (vineweave
   0.3415, geode 0.0749, mortis 0.1385, duskmaw 0.0432, the other five 1.0).
2. **Emission is `CUSTOM0.rgb x glow_energy`, not `x COLOR_0`.** In forge, Glow already holds the final emission colour: palette
   `emission x emission_scale` (`improve/palettes.py:22,127`). Every build wires Glow straight into Emission Color, with the
   material's Emission Strength and no Col multiply (`vineweave_build.py:434`, `duskmaw_build.py:1454`, ...). Import-notes
   item 1 says only "multiplies COLOR_1 into emission". `glow_energy` comes from the glb's `KHR_materials_emissive_strength`:
   1.5, except eldroot 1.4 and duskmaw 1.0 (no extension).
3. **blightcap model_scale 0.4 -> 1.0**, following the brief's implied-fit table. The old 0.4 compensated the old glb. With it
   reset, forge blightcap stands 1.569 m (the artist's forge size). If the artist still wants a "small mushroom", that is one
   line in the roster.

## Evidence
- Baseline `--import` before any change: exit 0, and the only warning is `ObjectDB instances leaked at exit`.
- Final clean `--import` (forge entries purged from `.godot/imported`): exit 0, 9x `Color1Ext: _GLOW -> CUSTOM0` + 9x
  `unit_glow_import: glow pass on <unit> surface 0`. Tail (same single warning as baseline):
  ```
  Unit Creator Tool unloaded
  WARNING: ObjectDB instances leaked at exit (run with --verbose for details).
     at: cleanup (core/object/object.cpp:2641)
  ```
- Headless dump in the GAME project (`res://game/characters/models/earth/geode_forge.glb`):
  ```
  UNIT geode ... size=(1.336618, 1.800018, 0.385848) h=1.8000 fp=1.3366 centre_xz=(0.0000,0.0000) min_y=0.0000
    mesh geode surf 0 verts=16934 CUSTOM0=true type=7 max_glow=1.7000 verts_gt1=2404 distinct_glow=7 skin=true blendshapes=2
      mat=StandardMaterial3D vcol_albedo=true emission_enabled=false albedo_tex=false
      next_pass=ShaderMaterial(res://game/visuals/units/unit_glow.gdshader, glow_energy=1.5)
    roster gem_knight model=res://game/characters/models/earth/geode_forge.glb yaw=0.0 scale=1.0 footprint=(1, 1)
  ```
  These match the forge gate exactly: 16934 verts, 2404 above 1.0, 7 distinct values, max 1.70. duskmaw: max 2.00, 3076 above 1.0,
  7 distinct. All 9 carry CUSTOM0 (type 7 = RGBA_FLOAT) and the glow pass, with stock emission off.
- GUT, 17 suites covering the changed roster/model fields (monster_kit, the 6 unit kits, skins, portrait_cache,
  match_loadouts, compendium, evolution_compendium, mapmaker_model, unit_facing, duskmaw_movement_sweep, unit_facing_live,
  overworld_content, element_preview_parity): **371/371 passed, 1925 asserts**. test_compendium reports 6 orphans
  (barkling_forge + 4). This is the existing leak: on the base model it reports 2 orphans (tree_grunt2 + 1), and the count is
  the model's node count.

## Findings / open items (report only)
- A stock import of these glbs would make the WHOLE unit glow: every forge material exports `emissiveFactor (1,1,1)` x strength
  1.5, and Godot imports it as `emission_enabled=true`. The import script turns that off. Any future forge glb in the game
  needs the same `import_script/path`.
- **Eldroot 5.10 m artist call:** the current asset is footprint-bound (fp exactly 3.8 at h 4.561). The `--no-cell-refit`
  5.10 m build would have fp of about 4.25 m, so under the game's 3.8 m boss-footprint ceiling it fits back to 0.895, which is
  4.56 m. The extra height only shows up if the boss footprint ceiling is also raised. The note is in `eldroot.tres` next to
  `footprint`.
- NOT render-verified (the headless dummy renderer cannot render): shader compile, additive depth-match on skinned meshes,
  and how the glow pass composes with the spent-wash and team outline. `dev_scripts/render_unit_facing.gd` and a live look are the
  orchestrator's or artist's call. The yaw 0 values rest on the forge facing checks plus the README convention and have not been looked at in-game.
- The extracted textures import as lossless with mipmaps (`detect_3d/compress_to=1`). The first editor open will probably flip them
  to VRAM-compressed and rewrite those 8 `.png.import` files.
- Not done (out of scope): geode idle emission pulse (item 2 shader-time fallback), vampwarrior toon/outlines, magmoo
  translucency, skins delivery, bastion/oakheart/undead models, the `UnitAnimator._get_mesh` capsule hit-flash bug (the flash
  `material_override` would also hide the glow pass for its duration if it ever hit the real mesh).
