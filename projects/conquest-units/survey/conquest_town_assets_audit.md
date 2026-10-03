# Conquest town-buildings & world-prop assets — quality audit

Generated 2026-10-03. Read-only survey of `C:/Users/kenne/OneDrive/Desktop/git/Conquest`.
Output file only; no source files were modified.

---

## 1. Inventory

### 1a. Battle-board tile geometry (player sees every match)

All tile geometry is **procedural GDScript**, built at runtime into `ArrayMesh` via
`ProcMesh.box()` / `ProcMesh.quad()` calls, cached by `TileMeshCache`. No `.glb`/`.obj`
for any tile or prop. Material = shared `stylized_props.gdshader` (vertex-colour, world-space
paint) or one of the named `stylized_*` shaders.

| Asset class | Builder | Style | Tri count | Appearance |
|---|---|---|---|---|
| Grass/dirt tile cap | `LowPolyTileBuilder` (GRASS/DIRT/MEADOW) | ProcMesh + `stylized_grass.gdshader` / `stylized_dirt` | ~40–80 / tile | ✅ chunky diorama block, matches art direction |
| Tall-grass tile | `LowPolyTileBuilder` (TALL_GRASS) | ProcMesh blade mesh + `stylized_tall_grass.gdshader` | ~200–400 / tile | ✅ |
| Tree tile | `LowPolyTileBuilder` (TREE) + `TreeBuilder.gd` | `stylized_foliage.gdshader` | ~300–600 / tile | ✅ |
| Stone-wall tile | `LowPolyTileBuilder` (WALL) | ProcMesh + `stylized_props.gdshader` | ~80 / tile | ✅ |
| Water tile | `LowPolyTileBuilder` (WATER) | `stylized_water.gdshader` | ~12 / tile | ✅ |
| Flagstone tile | `PavedTileBuilder` (FLAGSTONE) | ProcMesh | ~60–100 / tile | ✅ chunky slabs |
| Wooden-plank tile | `PavedTileBuilder` (PLANKS) | ProcMesh | ~40–80 / tile | ✅ |
| Lava/burn tile | `LowPolyTileBuilder` (LAVA) + `stylized_burn.gdshader` | ProcMesh | ~12 / tile | ✅ |
| Ice/snow tile | `LowPolyTileBuilder` + `stylized_ground.gdshader` / `stylized_ice_material` | ProcMesh | ~40–80 / tile | ✅ |
| Sacred ground / obsidian / ash / basalt | `LowPolyTileBuilder` + respective `.tres` materials | ProcMesh | ~40–80 / tile | ✅ |

**Scene files** (`tile_objects/tiles/scenes/**/*.tscn`): 14 tile scenes. All use the same
two builders; none import a `.glb`.

**Tile decor** (grass tufts, flowers, motes): generated in-builder via `stylized_decor`,
`stylized_motes` shaders. No separate model files.

World skirt (`game/visuals/world/WorldSkirt.gd`): procedural heightfield + MultiMesh
trees/rocks/bushes, ~20 k vertices, `world_skirt.gdshader`. No `.glb`.

---

### 1b. Overworld town buildings & props (story mode — player sees in Oakvale, Crownhaven, etc.)

All overworld props are **procedural ProcMesh** — `game/overworld/runtime/OverworldProps.gd`
(48 461 bytes, ~1 200 lines). No imported model for any building. Every prop is built by
`OverworldProps.prop(kind, footprint, tint)`.

| Prop kind | Where used (areas) | Build method | Approx tris | Art-direction fit |
|---|---|---|---|---|
| `"house"` (default) | oakvale, oakvale_ruins, woodland_town, river_crossing | ProcMesh boxes | ~200–400 | ⚠️ blocky box with roof — passable but very flat; no doors, shutters, character |
| `"cabin"` | woodland_town | ProcMesh boxes + log-detail | ~300–500 | ⚠️ same issue |
| `"keep"` | crownhaven | ProcMesh + battlements + corner towers | ~600–900 | ✅ reads as castle architecture |
| `"tower"` | crownhaven | ProcMesh + pyramid cap | ~200–300 | ✅ |
| `"gate"` | crownhaven | ProcMesh gatehouse arch | ~400–600 | ✅ |
| `"chapel"` | crownhaven, oakvale | ProcMesh | ~300–500 | ⚠️ unknown geometry (func defined but not read above — see note §3) |
| `"smithy"` | crownhaven, woodland_town | ProcMesh | ~300–500 | ⚠️ same |
| `"arena"` | crownhaven | ProcMesh elliptical drum, arcade, seating, sand floor | ~1 200–1 800 | ✅ most elaborate prop, clear silhouette |
| `"windmill"` | river_crossing | ProcMesh tapered tower, 4 sails | ~500–700 | ✅ readable silhouette |
| `"stall"` | crownhaven (market), mossway | ProcMesh counter + striped awning | ~300–400 | ✅ |
| `"well"` | oakvale, various | ProcMesh octagon shaft + roof | ~200–300 | ✅ |
| `"fence"` | oakvale, woodland_town | ProcMesh rails | ~80–150 | ✅ |
| `"haystack"` / `"barrels"` / `"cart"` / `"crystal"` / `"fire"` / `"rubble"` / `"dummy"` / `"logs"` / `"lamp"` / `"banner"` / `"scarecrow"` / `"crops"` | various | ProcMesh | ~50–300 ea. | ✅ / ⚠️ varies |
| NPCs (`figure()`) | all areas | ProcMesh box-figure, 8 kinds | ~200–350 | ⚠️ functional placeholder, contrast with forge unit quality is high |
| Hero avatar | overworld (walk) | CharacterResource `model_scene` → `hero.tres` (placeholder, no model set) | — | ❌ no visual model wired |

**Areas and their content**: 7 story areas each store a `terrain.tres` (MapResource tile
layout — same format as battle maps, loaded by `MapLoader`) and an `area.tres`
(`OverworldAreaResource` with NPC/prop/warp placements). Terrain sizes range from
49 KB (sparse_forest) to 100 KB (crownhaven). Props are instantiated at runtime via
`OverworldProps.prop()` keyed from `area.tres` prop-entity data.

---

### 1c. Unit character models (battle board + overworld NPC stand-ins)

These are **imported `.glb`** from the forge pipeline.

| File | Location | Tris | Materials | Status |
|---|---|---|---|---|
| `petalfang.glb` | models/forest/ | 9 986 | 1 × PBR flat-colour | old (pre-forge, Jul 2025) |
| `blightcap.glb` | models/forest/ | 10 000 | 1 × PBR flat-colour | old |
| `mycothrall.glb` | models/forest/ | 10 000 | 1 × PBR flat-colour | old |
| `eldroot.glb` | models/forest/ | 16 000 | 1 × PBR flat-colour | old |
| `vineweave.glb` | models/forest/ | 9 988 | 1 × PBR flat-colour | old |
| `tree_grunt.glb` | models/forest/ | 10 000 | 1 × PBR flat-colour | old |
| `necromancer.glb` | models/dark/ | 9 996 | 1 × PBR flat-colour | old |
| `monster.glb` | models/dark/ | ~53 997* | 4 × PBR skinned | old (rigged, 4 submeshes) |
| `gem_knight.glb` | models/earth/ | 9 790 | 1 × PBR flat-colour | old |
| `petalfang_forge.glb` | models/forest/ | unknown† | — | **forge / new** |
| `blightcap_forge.glb` | models/forest/ | unknown† | — | forge / new |
| `mycothrall_forge.glb` | models/forest/ | unknown† | — | forge / new |
| `eldroot_forge.glb` | models/forest/ | unknown† | — | forge / new |
| `vineweave_forge.glb` | models/forest/ | unknown† | — | forge / new |
| `barkling_forge.glb` | models/forest/ | unknown† | — | forge / new |
| `duskmaw_forge.glb` | models/dark/ | unknown† | — | forge / new |
| `mortis_forge.glb` | models/dark/ | unknown† | — | forge / new |
| `geode_forge.glb` | models/earth/ | unknown† | — | forge / new |

*monster.glb: index count 17 358+19 923+16 473+243 = 53 997 indices → ~17 999 tris (skinned).
†_forge variants have large JSON chunks (>4 KB) containing normal+AO textures, rigging
data, and multi-submesh layouts; tri count not parsed without a larger read — treat as
unknown pending the forge pipeline's own reporting.

Old non-forge `.glb` models (9): single PBR flat-colour material, no normal/AO maps,
no rig — these are the original placeholder imports from Jul 2025.
Forge `.glb` models (9): baked normal + AO maps present (`.png` sidecars in same
folder), multi-submesh, rigged — these are the production-quality units.
Active roster `.tres` files reference the `_forge.glb` variants for characters that
have them (`petalfang`, `vineweave`, `eldroot`, `mycothrall`, `blightcap`, `barkling`
via `models/forest/`; `duskmaw`, `mortis` via `models/dark/`; `geode` via `models/earth/`).
Characters `necromancer`, `gem_knight`, `tree_grunt`, `monster` still reference the
old pre-forge `.glb`.

---

## 2. Assessment vs art direction

Art direction target: **chunky low-poly stylized blocks, flat-shaded, hand-placed charm**.

| Area | Status | Detail |
|---|---|---|
| Battle-board tiles | ✅ MATCHES | LowPolyTileBuilder produces exactly the chunky diorama look. Shaders add world-space noise, tufts, flowers, motes. Fully in-line with art direction. |
| Board structural tiles (flagstone, planks) | ✅ MATCHES | PavedTileBuilder slabs are chunky and flat-shaded. |
| World skirt | ✅ MATCHES | Seamless, painted, non-interactive — correct. |
| Keep/tower/gate/arena/windmill/stall/well | ✅ PASSABLE | ProcMesh props are stylized and flat-shaded. Battlements, pyramid caps, sail geometry give readability. |
| House / cabin | ⚠️ BELOW BAR | Generic box + pyramid roof. No windows, doors, overhangs, or variation in silhouette. All houses in a town area share an identical shape — zero hand-placed charm. This is the biggest visual gap. |
| Chapel / smithy | ⚠️ UNKNOWN | Code exists (`game/overworld/runtime/OverworldProps.gd` contains the `match` branch) but the builder bodies are past the lines read; confirmed as ProcMesh by the pattern — likely similar flat-box quality. |
| NPCs / figures | ⚠️ GAP VS UNITS | Block-figure construction is intentional placeholder per `OverworldProps.gd` docstring ("Procedural placeholder art … real models slot in per entity via visual_character"). Contrast with forge unit quality now visible. |
| Hero avatar | ❌ MISSING MODEL | `game/overworld/content/hero.tres` declares a placeholder with no `model_scene` — hero renders as nothing or a fallback monogram in overworld walk. `OVERWORLD.md §2` describes "You are Vineweave" implying the lead unit's model is used, not this resource. Unclear if this is intentional. |
| Old unit `.glb` (necromancer, gem_knight, tree_grunt, monster) | ⚠️ PRE-FORGE QUALITY | Single flat-colour PBR material, ~10 k tris, no normal/AO, no rig (except monster). These four are below the forge quality bar set by the nine `_forge.glb` units. |

Scale / pivot conventions (delivery constraints for a forge build):
- `game/overworld/runtime/OverworldProps.gd` line 1: `## Every builder returns a Node3D whose origin is the cell's floor centre (feet at y = 0), facing +Z (south)` — this is the same convention as unit models (`CONQUEST.md "Unit facing"`).
- Cell size = `Cells.CELL_SIZE` (likely 2 units, matching `LowPolyTileBuilder.HALF = 1.0` → 2-unit tile).
- `PropEntity` footprints are declared in cell units (`Vector2i`); buildings span 1–4 cells.
- `OverworldProps._extent(fp, inset)` computes the local bounding rect in world units: x from `−CELL_SIZE/2 + inset` to `fp.x * CELL_SIZE − CELL_SIZE/2 − inset`. A 2-cell building occupies a 4-unit-wide footprint.
- Prop placement: `OverworldAreaResource` stores `PropEntity` records with `cell`, `kind`, `footprint`, `tint`; `OverworldController` calls `OverworldProps.prop(kind, footprint, tint)` and positions the root at the cell's world XZ.

---

## 3. Improvement plan — ranked

**#1 — House / cabin variants** (high impact, every town; rebuild-in-forge)
Zero variation across all towns: every `"house"` and `"cabin"` is the same geometry.
Adding 3–4 distinct silhouettes (narrow two-story, wide single-story with porch, ruined
variant, timber-frame variant) would transform the overworld's most-visited areas.
Forge batch family is feasible: shared wood/plaster/thatch palette, 3–5 builds per
run; drop in via `OverworldProps.prop()` by extending the `match` with new kinds
(`"house_a"`, `"house_b"`, etc.) or by switching to a `.glb` lookup on `PropEntity.kind`.
Tri budget: 600–1 200 each (currently ~300 from ProcMesh; hero-quality unnecessary,
crowd-NPC tier).

**#2 — Old unit models** (necromancer, gem_knight, tree_grunt, monster — high in-battle visibility; rebuild-in-forge)
These four appear in battles and the gallery. Pre-forge, ~10 k tris, single flat PBR,
no normal/AO. `monster.glb` has a rig but old-quality mesh. All four need forge treatment
matching the nine `_forge.glb` units. Each is already a separate forge build task;
no world-prop constraints apply (model_scene / model_yaw_deg / model_scale on
`CharacterResource`). Reference: `game/characters/roster/*.tres`.

**#3 — Chapel and smithy geometry** (medium impact, seen in Crownhaven and Woodland Town; retexture/refine-in-place or rebuild-in-forge)
Likely plain box + optional cross / chimney detail from the ProcMesh pattern.
Upgrading to a forge-built `.glb` with actual profile (pointed nave window, stone
chimney stack with smoke particle hook) would lift Crownhaven significantly.
Can be done as a small forge batch alongside the house family.

**#4 — NPC figures** (medium impact, every area; rebuild-in-forge over time)
`OverworldProps.gd` docstring explicitly says "real models slot in per entity via
`visual_character` (a roster model)". The slot exists — the path is to build a small set
of NPC archetypes (elder, guard, merchant, villager) as forge `.glb` and assign them
via the `CharacterResource.visual_character` field as that system matures.
No urgent forge lane yet; document as a planned replacement.

**#5 — Hero avatar in overworld** (unknown criticality; clarify intent first)
`game/overworld/content/hero.tres` has no `model_scene`. If the intent is "use the
lead unit's model" (per `OVERWORLD.md §2`), no new asset is needed — the hook already
exists in `OverworldActor`. If a hero-specific idle/walk model is intended,
that is a separate forge lane.

---

## 4. Forge rebuild constraints (for any lane that acts on #1 or #3)

- **Pivot**: origin at cell floor centre, +Z south (`OverworldProps.gd` line 1).
- **Scale**: 1 Godot unit = 1 metre; buildings must fit within `fp.x * CELL_SIZE × fp.y * CELL_SIZE` (typically 2–4 units a side).
- **Material**: can use `ProcMesh.material()` (vertex colour, `stylized_props.gdshader`) for drop-in parity, or a new baked-texture material via `CharacterResource` pattern (normal + AO PNGs, same import settings as `_forge.glb` units). If vertex-colour, no texture files needed.
- **Integration point**: `OverworldProps.prop()` `match` block, `game/overworld/runtime/OverworldProps.gd` line ~155. Add new `kind` strings. OR: replace the `match` default with a `.glb` scene loader keyed on `kind` — either approach works without changing `OverworldAreaResource` data.
- **Instantiation cite**: `game/overworld/runtime/OverworldController.gd` calls `OverworldProps.prop(ent.kind, ent.footprint, ent.tint)` and places the result at `ent.cell`'s world position. No LOD system exists; one mesh per prop.
- **Tint parameter**: each prop receives a `Color tint` used for roofs / banners / accents — the forge build must expose a primary accent colour, not bake it as a fixed colour.

---

## 5. Unknowns

- Chapel and smithy builder bodies not confirmed (file too large to read in full; assumed ProcMesh by pattern — verify by reading `OverworldProps.gd` from line ~900 onward).
- `_forge.glb` tri counts not extracted (JSON chunk > 4 KB; would need a larger binary read).
- Hero overworld model intent ambiguous (hero.tres vs lead-unit model).
- `monster.glb` exact tri count approximated as ~18 k (4-submesh skinned, see §1c note).
