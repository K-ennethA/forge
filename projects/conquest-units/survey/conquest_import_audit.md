# Conquest import audit (read-only, 2026-10-02)

Scope: map the Conquest game (C:\Users\kenne\OneDrive\Desktop\git\Conquest, branch `feat/foundation`, HEAD 232a5ab)
against the 16 built forge units. Nothing under Conquest was modified. Paths are relative to the Conquest repo unless
prefixed `forge:` (= C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units). Line numbers are from the files as read.

Working-tree state (observed with `git status`/`git diff`): `tools/blender/assets.conf` has an UNCOMMITTED edit (eldroot
height 3.4 -> 5.6, line 22 = the artist-authorized raise); `project.godot` and two Cinzel `.import` files show as modified
(line-ending warnings only, diff empty). The import lane branches from a dirty tree: its branch must not carry or revert these.

## 0. Pipeline facts the import lane must know

- Roster = one `CharacterResource` .tres per character in `game/characters/roster/` (12 files). `CharacterLibrary` scans the dir,
  id = filename (`game/characters/CharacterLibrary.gd:15`; `KNOWN_IDS` at :18 is a 6-id fallback only).
- Model fields on the resource (`game/characters/CharacterResource.gd`): `model_scene: PackedScene` (:21), `model_yaw_deg` (:~113 group
  "Model"), `model_scale` ("1.0 = the pipeline-fit size", about the feet), `footprint: Vector2i`.
- Spawn: `Unit._setup_character_model()` (`tile_objects/units/unit.gd:293`) instantiates `model_scene` as child `CharacterModel`, hides the
  capsule `MeshInstance3D`, calls `_orient_character_model` (:325-334): `m.position = footprint offset`, `m.rotation.y = UnitFacing.model_yaw(
  model_yaw_deg, facing)`, `m.scale = model_scale`. So a drop-in glb needs: root Node3D, feet at origin, +Z front after yaw, cell-fit size.
- The SAME fields are read by four more consumers: `game/ui/PortraitCache.gd:381-399`, `game/ui/menu/UnitPreview3D.gd:66-75`,
  `game/mapmaker/MapMakerScene.gd:1571-1598`, overworld `game/overworld/runtime/OverworldActor.gd:52-57` (+ `HeroResource.gd:16-18`).
  A roster-level yaw/scale correction therefore fixes all of them at once; a glb-level one does too.
- Existing game glbs came from `tools/blender/prepare_unit.py` / `export_rigged_unit.py` (cell-fit BAKED into the glb at export:
  `scale = min(target_h/H, max_fp/max(W,D))`, `export_rigged_unit.py:~25-32`, README `tools/blender/README.md` "Scale"). `assets.conf` lists only 5
  of the 9 shipped glbs (tree_grunt, petalfang, blightcap, eldroot, mycothrall; lines 19-23); vineweave, monster, necromancer, gem_knight are NOT in it.
- Animation bridge: `UnitAnimator` finds the first `AnimationPlayer` under the unit and plays clips named `idle/walk/attack/hit/death`
  (`game/visuals/UnitAnimator.gd:145-149`, `_find_clip` :1029 is case-insensitive, accepts `Armature|Idle`); missing clips fall back to tweens
  (README "Animations"). Forge glbs export `idle`+`walk` only (parsed from the 8 .glb JSON chunks), plus `ball` (magmoo) and `float` (supaoctto)
  which the bridge ignores. Attack/hit/death are deferred forge work (HANDOFF.md "Deferred waves") and fall back to tweens - not a blocker.
- Godot 4.6, Forward Plus (`project.godot:19`).

## 1. Roster <-> forge map

### 1a. Matched (9 of 12 roster ids; 9 of 16 forge units)

| roster id (file) | display | forge unit | current in-game asset | roster yaw / scale | forge cell-fit (check_*.json) -> implied scale* |
|---|---|---|---|---|---|
| `vineweave` | Vineweave | vineweave | `models/forest/vineweave.glb` (old model, no assets.conf line) | 0 / 1.0 (`vineweave.tres`) | h5.271 fp4.871 -> 0.3415 (H-bound, final h1.80) |
| `blightcap` | Blightcap | blightcap | `models/forest/blightcap.glb` (old) | **yaw 180 (:25), scale 0.4 (:26)** | h1.569 fp1.9 passes at 1.0 (final h1.569) |
| `petalfang` | Petalfang | petalfang | `models/forest/petalfang.glb` (old, --thorns 40) | 0 / 1.0 | h0.760 fp1.9 passes at 1.0 |
| `tree_grunt` | Barkling | barkling | `models/forest/tree_grunt.glb` (old) | 0 / 1.0 | h1.393 fp1.9 passes at 1.0 |
| `mycothrall` | Mycothrall | mycothrall | `models/forest/mycothrall.glb` (old) | **yaw 180 (:27)** / 1.0 | h0.481 fp1.9 passes at 1.0 |
| `eldroot` (boss, footprint 2x2) | Eldroot, the Hollow Crown | eldroot | `models/forest/eldroot.glb` (old) | 0 / 1.0, footprint 2x2 (:27-28) | cell_fit uses ceilings fp3.8/h3.4; h2.711 fp3.8 passes at 1.0. NB assets.conf (uncommitted) now says height 5.6 while forge check still uses 3.4 - reconcile |
| `gem_knight` | Geode | geode | `models/earth/gem_knight.glb` (old) | 0 / 1.0 | h24.04 fp17.85 -> 0.0749 (H-bound) |
| `necromancer` | Mortis | mortis | `models/dark/necromancer.glb` (old) | 0 / 1.0 | h13.0 fp8.0 -> 0.1385 (H-bound) |
| `monster` | Duskmaw | duskmaw (v3 latest) | `models/dark/monster.glb` (2.4 MB, rigged; Aug 5) | **yaw 180 (monster.tres:30) - STALE, see s.5** / 1.0 | h41.71 fp28.97 -> 0.0432 (H-bound, final fp1.25) |

*Implied scale = min(1.8/h, 1.9/fp) using the forge `cell_fit` check numbers; same formula the game pipeline bakes. Forge units are authored at
natural proportions (cell_fit is report-only in forge: godot-import-notes #4), so only barkling/blightcap/eldroot/mycothrall/petalfang already
arrive game-sized. All other forge glbs/blends need either a baked scale at delivery or a per-unit `model_scale` (see s.2 item 4).

Forge file availability for the matched 9: `.glb` exists only for duskmaw (`forge:rigged/duskmaw.glb`, v1-era name; latest build is
`duskmaw_v3.blend`, no v3 glb). barkling, blightcap, eldroot, geode, mortis, mycothrall, petalfang, vineweave have `.blend` only -
**the import lane must produce their glbs** (forge export is per-build-script `export_scene.gltf` in `forge:improve/*_build.py` for the 7 newer
units; none found for the 8 early units - unknown whether an exporter exists for them).

### 1b. Roster ids with NO dedicated forge unit (3 of 12) - placeholders reusing another unit's glb

| roster id | display | current in-game asset | yaw / scale | note |
|---|---|---|---|---|
| `bastion` | Bastion | reuses `forest/eldroot.glb` (`bastion.tres:4`) | 0 / **0.55** (:25), footprint 1x1 | defensive stationary earth unit; no forge model, no sheet |
| `oakheart` | Oakheart | reuses `forest/tree_grunt.glb` (`oakheart.tres:4`) | 0 / **1.3** (:27) | Barkling's evolved form (`game/characters/evolutions/tree_grunt__oakheart.tres`); no forge model, no sheet |
| `undead` | Undead | reuses `forest/mycothrall.glb` (`undead.tres:4`) | **180** (:25) / 1.0 | excluded from character select (`menus/CharacterSelect.gd:46`); also used as story raider placeholder (`game/overworld/build/build_story_content.gd:116`) |

### 1c. Forge units with NO roster id (7 of 16)

firefly, firesprite, magmoo, supaoctto, vampito, vampwarrior: no `CharacterResource` exists (grep of `game/`, `docs/`, `dev_scripts/` finds no
reference to any of these names). Adding one = a new roster .tres (stats, moves, element, abilities are GAME DESIGN the import lane must not invent -
unknown/artist-owned) - the forge deliverable is the model + contract only. `wren`: not a roster id either; Wren is the story hero
(`game/overworld/data/HeroResource.gd:14-18`, default name "Wren"), presently a PLACEHOLDER using `vineweave.glb` at yaw 0 / scale 1.3
(`game/overworld/build/build_story_content.gd:118,258-260`; saved to `game/overworld/content/hero.tres`). Swapping Wren in is a HeroResource data
change (`model_scene` + yaw + scale), per `HeroResource.gd:7-8`. Wren is also planned as a human BATTLE unit (docs/design/STORY.md s.2.1) - no
battle roster entry exists yet.

Counts: roster 12 = 9 matched + 3 placeholder-reuse; forge 16 = 9 matched + 7 unrostered (6 creature/character units + Wren-as-hero).

### 1d. Skins (relevant to godot-import-notes #3)

12 skin resources in `game/skins/content/` (2 each for blightcap, gem_knight, mycothrall, necromancer, petalfang, vineweave). All are tint-only
(e.g. `vineweave_emberroot.tres:13` `tint = Color(1, 0.45, 0.35, 1)`; `gem_knight_sapphire.tres:13`). Forge has skin variants as separate .blends:
`geode__amethyst, firefly__ember, firesprite__soulfire, magmoo__obsidian, supaoctto__deepsea/__starfish, vampwarrior__dawn, vineweave__emberroot,
wren__winter` (+ `supaoctto__starfish_floating`). Only vineweave_emberroot (and geode's recolor family) overlaps an existing game skin id; the rest are new.

## 2. The six import-notes items, grounded

### Item 1 - COLOR_1 glow shader (geode, vineweave, mortis; also firefly/firesprite/magmoo/supaoctto/vampwarrior/wren per glb extensions)

- Every forge glb carries `COLOR_0` AND `COLOR_1` (verified from the 8 glb JSON chunks: attrs COLOR_0,COLOR_1,JOINTS_0,...) and 6 of 8 list
  `KHR_materials_emissive_strength`.
- **UNVERIFIED, HIGH RISK (assumption, from memory of Godot's glTF importer, not tested here):** Godot's `ArrayMesh` has a single color array and the
  stock importer reads only `COLOR_0`; `COLOR_1` is likely dropped at import, which would defeat the recommended shader. Settling experiment: a
  headless import of `forge:rigged/vampwarrior.glb` (which has COLOR_1) into a scratch project and dump `mesh.surface_get_arrays()` for ARRAY_COLOR /
  ARRAY_CUSTOM0-3. If dropped, the fallbacks are (a) forge writes glow into UV2/TEXCOORD_1 or a custom `_GLOW` attribute (Godot maps `_`-prefixed
  custom attributes to CUSTOM channels in recent versions - also unverified) or (b) the baked emissive texture option. This decides the forge export
  format and must be answered before any delivery lane.
- Where the shader would live: no unit-material shader exists today. Shader precedent: `tile_objects/tiles/shaders/*.gdshader` (all tile/prop/world;
  `stylized_props.gdshader:1-2` is `diffuse_toon, specular_toon` reading vertex `COLOR` - closest precedent) and `game/visuals/weather/weather_overlay.gdshader`.
  Code precedent for building a vertex-colour ShaderMaterial with a StandardMaterial3D fallback: `game/visuals/ProcMesh.gd:15-40`. Proposed additive
  location (matches repo layout): `game/visuals/units/` (new dir) for `unit_glow.gdshader` + a material-builder, applied by an additive hook.
- How a material would be attached with NO change to `unit.gd`: only the character-model build path (`unit.gd:293-318`) instantiates the PackedScene.
  Cheapest drop-in = deliver per-unit wrapper scenes (`.tscn` inheriting the glb) with the ShaderMaterial already set as surface override, and point
  `model_scene` at the wrapper (no code change). Alternative = a small post-instantiate hook in `_setup_character_model` (touches `unit.gd`, not additive).
- Interactions to respect (all in `game/visuals/`, observed in code, not run):
  - `UnitVisualManager.apply_acted_visual` sets `material_overlay` + `GeometryInstance3D.transparency` on EVERY mesh under `CharacterModel`
    (`UnitVisualManager.gd:427-434`): team outline + spent-wash. Composes over ShaderMaterials; whether `transparency` fade composes with a custom
    spatial shader is not verified here (Godot handles `GeometryInstance3D.transparency` on spatial materials; an explicit test of a spent unit is needed).
  - `UnitAnimator._flash` (hit) and heal flash replace `material_override` with a StandardMaterial3D on `_get_mesh(unit)`
    (`UnitAnimator.gd:629-644`, `:691-706`). `_get_mesh` (:924-932) returns the direct child named `MeshInstance3D` FIRST - that is the hidden capsule
    for glb units - so the flash may be targeting an invisible mesh already (OBSERVED in code, not run; flag for the import lane to check the live
    hit flash on any glb unit). Not a shader blocker either way.
  - `Unit._apply_skin_tint` / `SkinLibrary.tinted_material` (`game/skins/SkinLibrary.gd:145-155`) only tint `StandardMaterial3D`/`ORMMaterial3D` albedo; a
    `ShaderMaterial` is returned duplicated but UNTINTED. A shader-material unit will silently ignore tint-only skins (see item 3).

### Item 2 - idle emission-pulse via KHR_animation_pointer (geode)

- Importer support in Godot 4.6: UNKNOWN. Settling experiment: headless import of a geode glb with the pointer extension, then list the
  `AnimationPlayer` tracks (a material-property track path vs no track). Forge `geode.glb` does not exist yet (only `geode.blend`,
  `rigged/geode_gltf_probe.json`).
- Fallback is shader-side time pulse (item 1's shader gets a `pulse` uniform and `TIME`). That keeps the pulse independent of `UnitAnimator`'s clip
  bridge, which only plays `idle/walk/attack/hit/death` by name (`UnitAnimator.gd:145-149`) and does not understand material tracks.
- Clip `idle` is the only animated driver; `UnitAnimator._queue_idle` (:1078) keeps it looping, so a pointer-driven pulse would ride it for free if supported.

### Item 3 - per-skin delivery shape (N glbs vs 1 glb + material overrides)

- Game already supports BOTH shapes in data: `SkinResource.model_scene` = full model swap through the same orient/scale pipeline
  (`SkinResource.gd:~70`, `Unit.apply_equipped_skin` `unit.gd:~380` -> `_rebuild_character_model` :410-428), and `tint` = albedo multiply (StandardMaterial3D only).
- No "material override resource" slot exists on SkinResource. With ShaderMaterial units (items 1/5/6), the tint path does nothing (see item 1), so
  forge skins (`geode__amethyst`, etc.) have exactly two game-compatible shapes: (a) N glbs -> one `SkinResource.model_scene` each (works today with zero
  game change) or (b) one glb + an additive `SkinResource` field (e.g. `material_overrides`) - touches `SkinResource.gd`/`unit.gd`, NOT additive.
  Recommend (a) for the first wave; decide (b) only after the COLOR_1 probe (item 1) shows what palette repaint actually needs.
- Skin ids are persistent profile keys (`SkinResource.gd` "must never change once shipped"); forge skins that reuse an existing id must keep it.

### Item 4 - per-unit import scale

- Configured in three places today: (i) baked into the glb at export (`assets.conf` col 4 `height` + `--max-footprint`, executed by `reingest.sh`; ceilings
  1.8/1.9, boss 3.4-5.6/3.8); (ii) `CharacterResource.model_scale` (about feet, `unit.gd:328-333`; used today as a compensation knob: blightcap 0.4, bastion 0.55,
  oakheart 1.3); (iii) glb `.import` `nodes/root_scale=1.0` for all 9 shipped glbs (`game/characters/models/*/*.glb.import:20`) - untouched, available as a third lever.
- `HeroResource.model_scale` and `SkinResource` (inherits the character's scale: `_orient_character_model` reads the CHARACTER's `model_scale` even for a skin model)
  are the other two. Note the last: a skin `model_scene` cannot carry its own scale.
- Implied fit scales per forge unit are in the s.1a table and s.1c-units: duskmaw 0.0432, firefly 0.2396, firesprite 0.4201, geode 0.0749, magmoo 0.1075
  (FP-bound -> final height only 0.576: the open "magmoo scale question" in HANDOFF.md), mortis 0.1385, supaoctto 0.0839, vampito 0.0906 (FP-bound, final h1.096),
  vineweave 0.3415, vampwarrior 0.978, wren 1.028. Natural-size units (barkling, blightcap, eldroot, mycothrall, petalfang) = 1.0.
- Stale compensations to reset when swapping to forge models (they compensate the OLD glbs): blightcap `model_scale 0.4`, bastion 0.55, oakheart 1.3.
  Reset-vs-keep is an artist/design call (a 0.4 blightcap is a deliberate "small mushroom" read per `CharacterResource.gd` doc), not a mechanical fix.
- Recommended delivery shape: bake the fit scale into the delivered glb (as the game's own pipeline does; keeps `model_scale = 1.0` = "pipeline-fit"
  semantics, keeps rig/animation consistent - `export_rigged_unit.py` scales the RIG), and leave `model_scale` for design intent only.

### Item 5 - translucent magmoo (alpha blend + depth prepass)

- Forge glbs with `alphaMode BLEND`: magmoo, firefly, firesprite (parsed from glb JSON; vampito/duskmaw/supaoctto/vampwarrior/wren opaque). Item 5's
  note names only magmoo; firefly/firesprite also use BLEND - check whether they need the same prepass (their meshes are 2 each).
- Conquest has no unit-translucency precedent beyond `UnitVisualManager`'s spent fade (`transparency` on all meshes, :63,:434, which already depends
  on transparent sorting) and the placeholder death fade (`UnitAnimator.gd:765-778`: `TRANSPARENCY_ALPHA` flash material). A depth-prepass unit under
  `material_overlay` (team outline) is untested: an overlay on an alpha-prepass surface may double-blend (UNKNOWN; verify in the render_unit_facing roster grid).
- The spent-wash/outline `material_overlay` is applied to EVERY mesh including magmoo's 5 skinned meshes. No game change needed for the shader
  itself: it is a material property delivered with the model (see item 1 wrapper-scene route).
- `render_unit_facing.gd` (`dev_scripts/render_unit_facing.gd`, headless/xvfb usage in its header) is the existing roster render harness - the import lane's
  verification tool - but "never launch windowed" applies to this session; the harness needs a rendering driver, so it is the orchestrator's call.

### Item 6 - vampwarrior toon shader + outline shells

- Toon precedent exists: `stylized_props.gdshader` uses `render_mode diffuse_toon, specular_toon` (built-in Godot toon) - NOT a ramped 3-step; the 0.58/0.84/1.0
  ramp from the notes would be a new shader. One shared toon+glow shader (items 1 and 6 merge) under the same `game/visuals/units/` location.
- Outline shells: Conquest already draws a team-colour outline through `material_overlay` (`UnitVisualManager.gd` `_get_overlay_material`, :596-650, "outline" side per
  team) on ALL meshes. A vampwarrior with baked inverted-hull shells would have shells plus the team overlay (the overlay also lands on the shell meshes):
  double outline + overlay on shells. UNKNOWN visual result; the notes' alternative (next_pass cull_front grow shader, forge stops exporting shells) avoids this and
  returns ~6.4k tris. Decision for the artist/import lane; flag both.
- Shells are separate MeshInstance3D nodes in the glb (vampwarrior.glb has 2 meshes, 1 skin); hiding/swapping the sword outline with the sword needs a node-path
  contract (names `vampwarrior_outline`, `vampwarrior_sword_outline` per notes) - none exists game-side; wrapper scene is the place.
- Facing/yaw: vampwarrior check passes `facing_-Y` at yaw_fix 0 (`forge:rigged/check_vampwarrior.json:30`) -> roster yaw 0.

## 3. Roster characters with NO forge model AND NO character sheet

Sheets/reference that exist in `forge:design/reference/` (and `forge:design/refs/`): `firefly-character-sheet.webp`, `firesprite-character-sheet.webp`,
`firesprite-sketch.webp`, `wren-character-sheet.webp`, magmoo sketches/references, annotation PNGs for duskmaw/supaoctto/vampwarrior/eldroot. Elias exists only as an
inline transcription in `forge:design/review-log.md:1155-1180` (not a roster id; not built). Forge units built from the artist's own sculpt files
(source-copies: hero-*, ancient_tree, flower_grunt, parasite_grunt, shroom_grunt, tree_grunt, newunit-*) need no sheet.

Roster characters with neither a forge model nor a sheet (3; artist supplies designs):
1. **bastion** - placeholder = eldroot.glb @0.55 (`bastion.tres:4,25`)
2. **oakheart** - placeholder = tree_grunt.glb @1.3 (`oakheart.tres:4,27`); evolved form of Barkling
3. **undead** - placeholder = mycothrall.glb yaw 180 (`undead.tres:4,25`); select-screen-excluded; used as story raider placeholder

Not roster characters but ALSO have no model/sheet (observed from game content; the artist may or may not want them): the story's NPC cast (Briony, Tobin,
Hessa, Rowan, Linnea, Astrael the Fallen Star: `docs/design/STORY.md` s.1-2; NPC entities in `game/overworld/data/NpcEntity.gd` hold no model field - grep for
model/glb/character_id returns nothing), and the Gloam/Cindral enemy units ("PLACEHOLDER raider units until Cindral's own units exist",
`build_story_content.gd:115`). Listed for completeness; scope decision belongs to the artist.

## 4. Where an additive import branch lives (dir conventions)

- Models: `game/characters/models/<biome>/<id>.glb` (+ Godot-generated `.glb.import`); existing biomes `forest/`, `dark/`, `earth/`. Files are named by
  asset name, NOT roster id (`tree_grunt.glb` -> id `tree_grunt`; `monster.glb` -> `monster`; `gem_knight.glb` -> `gem_knight`; `necromancer.glb`).
  Forge ids differ (barkling/duskmaw/geode/mortis) - the contract doc must carry the forge-name <-> roster-id table (s.1a is that table).
- Additive-collision rule: forge glbs would NOT overwrite the existing files (user rule: never modify their source assets). Use a new directory with no
  collisions, e.g. `game/characters/models/forge/<forge_id>/<forge_id>.glb` or a parallel `<id>_forge.glb`; the swap is then a one-line change in the roster
  .tres `model_scene` + `model_yaw_deg` + `model_scale`, which is a MODIFICATION of existing roster files. The strictly additive route that avoids even that
  is the werewolf pattern: a contract path the game checks (see below) - NOT present in Conquest today (`Unit._setup_character_model` has no such branch).
- Roster: `game/characters/roster/<id>.tres` (new ids = new files; scanned automatically). Skins: `game/skins/content/<skin_id>.tres`. Evolutions:
  `game/characters/evolutions/*.tres`. Hero: `game/overworld/content/hero.tres` (generated by `build_story_content.gd`, i.e. regenerating overwrites hand edits - the
  hero swap should edit the builder constants `HERO_MODEL` / yaw / scale at `build_story_content.gd:118,259-260`).
- Shaders/materials: no unit-shader dir exists; propose `game/visuals/units/` (additive). Existing shader homes are tile-scoped:
  `tile_objects/tiles/shaders/`, `game/visuals/weather/`, `board/selected_shader.gdshader`.
- Tests that touch roster/model fields: `tests/unit/test_unit_facing.gd` (pins `UnitFacing.model_yaw` math only, line 73 - not specific roster yaws),
  `tests/unit/test_skins.gd`, `tests/unit/test_portrait_cache.gd`, `tests/unit/test_mapmaker_model.gd`, `tests/unit/test_compendium.gd` (per EVOLUTION.md: fails if any unit is missing).
- Tools: `tools/blender/{prepare_unit.py,export_rigged_unit.py,reingest.sh,assets.conf}`; project skill `.claude/skills/blender-unit-import/SKILL.md` documents the
  current import recipe (the forge import should not edit these; it adds its own contract doc, as the werewolf does).
- Pattern of record (werewolf): `werewolf/docs/design.md:48` - "if `res://assets/characters/<kind>/<kind>.glb` exists, CharacterModel instantiates it and drives
  its AnimationPlayer with the clip names below; otherwise it builds the procedural figure ... no game code change to swap art." Conquest's analogue needs
  ONE new branch in `unit.gd`/a model-resolver autoload (the only non-additive piece), or the roster-.tres edits above. Which of the two the user prefers is OPEN.
- Branch note: current branch is `feat/foundation` with ~11 other feature branches (`feat/duel`, `feat/overworld`, `feat/party-duels`, ...). The import branch should be cut
  from the commit the user names; the dirty `assets.conf` eldroot edit must not be swept in.

## 5. Duskmaw yaw: the stale 180

- Location: **`game/characters/roster/monster.tres:30` - `model_yaw_deg = 180.0`** (`character_id = &"monster"`, display "Duskmaw").
- The shipped `game/characters/models/dark/monster.glb` already faces +Z natively: `forge:improve/duskmaw_facing.json` - mouth direction
  `-2.1 deg` from -Y in the Blender-imported frame (= front/+Z in Godot), `177.9 deg` AFTER the roster's 180 (i.e. facing AWAY from the camera). The same
  report's axis probe confirms Blender -Y exports to glTF +Z. So the 180 compensates a problem that does not exist in the shipped glb (or was added when the
  old model faced backward and the model was later re-exported).
- Forge duskmaw v3 is -Y front already: `forge:rigged/check_duskmaw.json` `facing_-Y` pass, angle -0.2 deg, `yaw_fix_deg 0.0`. On swap, `model_yaw_deg` must be 0.
- Nothing in `monster.glb.import` is yaw-related (`nodes/root_scale=1.0`, no rotation params). No other config location for duskmaw's yaw was found;
  `tests/integration/test_duskmaw_*.gd` exist but pin movement/canto, not yaw.
- Same-class risk: `blightcap.tres:25`, `mycothrall.tres:27`, `undead.tres:25` also carry 180. Forge blightcap and mycothrall passed `facing_-Y` only
  AFTER a forge-side 180 correction was baked into the improved copy (`forge:rigged/check_blightcap.json:30`, `check_mycothrall.json:30` `yaw_fix_deg 180.0`;
  the forge source copies were facing +Y), so for forge blightcap/mycothrall the roster yaw must also be reset to 0 on swap. (Petalfang/supaoctto/vampito also
  show yaw_fix 180 in forge checks; petalfang's game roster yaw is already 0, meaning the OLD game petalfang glb was exported correct and the forge copy needed
  the fix independently - consistent.) Verify visually with `dev_scripts/render_unit_facing.gd` per `CONQUEST.md` "Unit facing".

## 6. Blockers / unknowns for the import lane

1. COLOR_1 survival through Godot's glTF importer - UNVERIFIED and load-bearing for the glow shader and for every "palette repaint = re-export" claim. Needs the one
   headless probe in item 1 before any delivery format is chosen.
2. KHR_animation_pointer import in Godot 4.6 - UNKNOWN (item 2). Fallback exists, no blocker.
3. Only 1 of the 9 matched units has a .glb (duskmaw, v1 name); 8 need glb export and the exporter for the early units was not found in `forge:improve/` (unknown
   whether a shared exporter exists; per-unit `_build.py` scripts do the export for firefly, firesprite, magmoo, supaoctto, vampito, vampwarrior, duskmaw).
4. Which import shape the user wants: contract-path branch in `unit.gd` (new code in an existing file) vs roster-.tres edits (modifies 9 existing files) vs
   wrapper `.tscn` per unit. All three keep `unit.gd` untouched except the first. Recommend wrapper-scene + roster `model_scene` swap on the import branch.
5. Skin tint is StandardMaterial3D-only (`SkinLibrary.gd:145-155`): shader-material units lose tint skins; forge skins ship as separate glbs for now.
6. The roster has no entries for 6 forge creature units (firefly, firesprite, magmoo, supaoctto, vampito, vampwarrior); stats/moves/abilities are game design,
   unknown/artist-owned. Models can be delivered ahead; roster entries cannot be invented by the import lane.
7. Eldroot ceiling mismatch: uncommitted `assets.conf` height 5.6 vs forge `cell_fit` ceilings 3.4 (forge:rigged/check_eldroot.json).
8. `UnitAnimator._get_mesh` hit flash may hit the hidden capsule for glb units (observed in code, not run); not caused by forge, but the import lane will see it.
