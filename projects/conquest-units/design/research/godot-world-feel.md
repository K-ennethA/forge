# Godot world feel: Conquest vs modern Pokemon / Fire Emblem (gap analysis + ranked plan)

Research lane, 2026-10-03. Read-only on Conquest (branch `feat/forge-unit-import`). The artist asked:
"is it possible for us to replicate the feel and art level of a more modern pokemon/fire emblem game
... our models arent on that level for the world assets of conquest, and there are things we can do
better in godot to make the game feel better."

Labels: **[SOURCED]** = from a cited doc/talk/analysis. **[CODE]** = read in Conquest at file:line.
**[ASSUMPTION]** = inferred, not measured; each one names its settling experiment. No Godot window was
launched, so nothing here is backed by a render. Lanes cannot render: Godot `--headless` uses the dummy
renderer, so every look experiment below is an **artist A/B in one playtest launch** (see the A/B toggle
in Lane 1).

---

## 0. Baseline: what Conquest renders with today

| Fact | Where |
|---|---|
| Renderer: **Forward+** on desktop. No `rendering_method.mobile` override, so the Android export falls back to Godot's default **Mobile** renderer | `project.godot:19` (`"4.6", "Forward Plus"`); `export_presets.cfg:70-71` (Android stub) |
| `[rendering]` sets only `vram_compression/import_etc2_astc=true`. **No MSAA, no FXAA/SMAA, no TAA, no debanding, no shadow-quality overrides** | `project.godot:197-199` |
| MSAA 4x is used only for the menu preview SubViewports | `game/ui/menu/MapPreview3D.gd:87`, `UnitPreview3D.gd:32` |
| All look code lives in one node, `WorldLook`, which the battle and the overworld both use | `game/visuals/world/WorldLook.gd`; created by `GameWorldManager.gd:568-588` and `OverworldController.gd:121-127` |
| Key light: warm sun `(1.0,0.93,0.8)` x 1.55, pitch -50 / yaw -36, PSSM **4 splits**, max distance **140**, blur 1.6 | `WorldLook.gd:18-34, 195-204` |
| Fill: ambient comes **from the procedural sky** (`AMBIENT_SOURCE_SKY`, energy 0.62, sky contribution 0.85). Day sky is blue-grey: top `(0.42,0.6,0.78)`, horizon `(0.74,0.82,0.86)`, ground = haze `(0.66,0.75,0.8)` darkened | `WorldLook.gd:35-52, 218-225` |
| The sky is never on screen: both cameras sit at a fixed 50 deg pitch with a 50 deg vertical FOV, so the top of the frame points 25 deg below the horizon. The sky works only as the ambient (fill) source | `GameWorld.tscn:43-46`; `OverworldCamera.gd:13-14, 30-32`; `CameraController.gd:832,1201` (it never changes the camera's basis or FOV) |
| Tonemap **Filmic, white 6.0**, exposure 1.0 | `WorldLook.gd:226-228` |
| Glow: intensity 0.55, bloom 0.04, HDR threshold 0.95, blend mode **forced to SOFTLIGHT**, glow levels left at the defaults | `WorldLook.gd:229-233` |
| Adjustments: saturation 1.0, contrast 1.03, **no color-correction LUT** | `WorldLook.gd:234-237` |
| Fog: **depth** fog from 70 to 240, curve 1.6, no aerial perspective. The far haze is done mostly in shaders (`apply_outside`) | `WorldLook.gd:240-248`; `world_lib.gdshaderinc:109-123` |
| SSAO is on (radius 0.8, intensity 1.4). The code comment says it is "ignored elsewhere", i.e. lost on Mobile | `WorldLook.gd:249-254` |
| No DOF / CameraAttributes, no SSIL/SDFGI/VoxelGI/Lightmap, no fresnel on units, no cloud shadows, no LUT. The only vignette is in the rain weather overlay | grep across the project (worktrees excluded) |
| World shaders all use `diffuse_toon` and world-space paint. Wind sway is already built (foliage, tall grass, decor). Foliage has a warm rim. Props fake contact AO in the shader | `stylized_props.gdshader:2, 61-63`; `stylized_foliage.gdshader:44-68, 139-141` |
| Units: StandardMaterial3D (vertex-colour albedo, roughness 1) plus an additive unshaded glow next_pass with unclamped HDR `CUSTOM0`. **Not toon-lit, no rim** | `game/visuals/units/unit_glow.gdshader:16-28`; `unit_glow_import.gd:9-12` |
| Weather already blends looks through `WorldLook.apply_weather_look` (good: one owner) | `WeatherEnvAdapter.gd:113-118` |
| Town props: every one is ProcMesh. Houses are identical boxes with pyramid roofs (~200-400 tris). Lamps and fires are OmniLights | audit `survey/conquest_town_assets_audit.md` §1b/§2; `OverworldProps.gd:665, 683, 995` |

**Three load-bearing findings**
1. **No anti-aliasing anywhere in the game view.** Flat-shaded, hard-edged low-poly geometry with toon light steps is the worst case for stair-stepping and shimmer. This is the cheapest, biggest "looks cheap" factor. [CODE `project.godot:197-199`]
2. **Glow is pinned to the mode Godot 4.6 moved away from.** In 4.6, glow blends *before* tonemapping for every mode *except* soft light, and the default became Screen. The 4.6 PR says soft light "removes the glow effect when blending against dark backgrounds" [SOURCED: godot PR #110671; 4.6 release notes]. `WorldLook.gd:233` forces SOFTLIGHT, so the forge units' glow next_pass and the fire/lava/magic emissives get the weakest bloom Godot offers.
3. **The fill light is a cool, grey sky the player never sees.** Shadowed sides take 85% of their light from a desaturated blue-grey hemisphere, and the lower half is grey haze. In battle the sky colours *are* the shadow colours. No LUT, Filmic at white 6, and saturation 1.0 do nothing to warm or richen them. This is the opposite of the warm-amber FE direction the UI was approved on. [CODE `WorldLook.gd:40-52, 218-225`; effect on the image = ASSUMPTION, see T3]

---

## 1. Per-topic analysis

Format: reference practice -> Conquest today -> **verdict** -> Godot change -> Mobile renderer -> settling experiment.

### T1. Anti-aliasing / image stability
- **Reference:** Digital Foundry singled out Legends: Arceus for having "no anti-aliasing" among its failures [SOURCED: DF via NintendoWire]. Image stability is part of what reads as "modern".
- **Conquest:** none. [CODE `project.godot:197-199`]
- **Verdict: GAP.**
- **Change:** `rendering/anti_aliasing/quality/msaa_3d = 4x` on desktop. Add a `.mobile` override of 2x MSAA, or SMAA/FXAA (`screen_space_aa`). Turn on `use_debanding` (smooth fog/sky gradients band at 10-bit). Foliage that dithers or alpha-cuts needs alpha-to-coverage to benefit from MSAA.
- **Mobile:** MSAA 3D, FXAA and SMAA are all supported. TAA is Forward+ only. Godot's docs say "2x MSAA may be usable ... higher MSAA levels are unlikely to run smoothly on mobile GPUs" [SOURCED: 3D antialiasing docs].
- **Experiment:** artist A/B in one launch, AA off vs 4x (toggle in Lane 1). Perf: compare frame time on the target phone, 2x MSAA vs SMAA, once the Android preset is real.

### T2. Bloom tuned for emissives
- **Reference:** HD-2D-style looks apply bloom only to genuinely bright sources (torches, embers, spells), together with tilt-shift DOF and a vignette [SOURCED: HD-2D recreation notes, see sources]. Glow sells the magic, the fire and the unit glow.
- **Conquest:** SOFTLIGHT, intensity 0.55, threshold 0.95. [CODE `WorldLook.gd:229-233`]
- **Verdict: GAP** (finding 2).
- **Change:** `GLOW_BLEND_MODE_SCREEN` (4.6 default). Start from 4.6's defaults: intensity 0.3, levels `{0, 0.8, 0.4, 0.1, 0, 0, 0}` [SOURCED: PR #110671 table]. Set the threshold so lit albedo never blooms and only emissives do: ~1.0 to 1.2, which still sits under the 2.0 Mobile ceiling. Re-derive the weather `glow_scale` multipliers once. `apply_weather_look` already scales relative to the base, so no other code changes.
- **Mobile:** glow is supported and "much faster" in 4.6 [SOURCED: 4.6 release]. **Hard constraint:** the Mobile framebuffer is RGB10A2 with a 0 to 2.0 range ("a pixel can't get brighter than Color(2,2,2)") [SOURCED: godot #101558 discussion; renderers docs "medium dynamic range, low precision"]. Forge glow values above 2.0 (`CUSTOM0` x `glow_energy`, unclamped, `unit_glow.gdshader:27`) clip on phones, so a unit that blooms hard on desktop goes flat on mobile. **Rule for the forge palette: keep emissive peaks at 2.0 or below** and get "brighter" through glow intensity, not emissive magnitude.
- **Experiment:** A/B SOFTLIGHT vs SCREEN on a night map with a glowing forge unit (Mortis/Duskmaw) and a fire tile. [ASSUMPTION that the forge `_GLOW` peaks exceed 2.0: settle it by reading max `_GLOW` across the 9 `_forge.glb` files with a Python glb parse, no Godot needed.]

### T3. Key/fill balance, warm grade, saturated bounce
- **Reference:** a warm key against a cool fill is the stock stylized recipe. One Godot community tip is to mix "blue or purple into the ambient light as a contrast to a slightly yellow direct light", with fairly high ambient for low-poly [SOURCED: Godot forum low-poly lighting]. FE Engage was art-directed to be "colorful ... vivid, and really popped" [SOURCED: Nintendo Ask the Developer vol. 8]. [ASSUMPTION, from looking at screenshots rather than developer data: FE/Pokemon shadows are tinted and saturated, never grey, and the key:fill ratio is low (about 2-3:1), so nothing reads muddy.]
- **Conquest:** warm key x 1.55. The fill is a grey-blue sky with a grey lower hemisphere, ambient 0.62 x 0.85. No LUT. [CODE `WorldLook.gd:18-52, 218-237`] Rough estimate: lit ≈ 1.55 + ~0.3 ambient vs shadow ≈ 0.3, about **6:1**, which reads as deep, desaturated shadows. [ASSUMPTION: settle it with a probe below.]
- **Verdict: GAP.**
- **Change (all in `WorldLook`):** (a) re-tint the fill. Saturated sky-blue top. Warm the lower hemisphere (`ground_horizon/bottom_color`) toward a grass-green/sand bounce instead of grey haze, so shadows pick up colour. Mix in `ambient_light_color` (the 15% non-sky part is currently black). (b) Lower the key:fill toward ~3:1 (key ~1.2-1.3, ambient ~0.8). (c) Add a **3D LUT** (`adjustment_color_correction`) per lighting preset: warm highlights, slightly lifted coloured shadows, a gentle saturation push in the mids. The forge can generate the LUT PNGs headlessly from a parametric grade, so they stay reproducible. (d) Tonemap A/B. Godot's Filmic curve at white 6 maps albedo-1.0 fully lit to about 0.66 linear (worked from Godot's `tonemap_filmic` constants: f(1)/f(6) = 0.578/0.873). That shoulder is what makes brights look milky and desaturated. Try Filmic with white about 2.5-3 plus exposure trim, or AgX (4.6 exposes `agx_white`/`agx_contrast`; AgX is forced to white 2.0 on RGB10A2 [SOURCED: PR #106940]). [ASSUMPTION that 4.6's Filmic constants match the ones used here.]
- **Mobile:** sky ambient, adjustments, LUT and tonemap are all supported. Below 2.0 the tonemap curve is identical on both renderers.
- **Experiment:** one launch with Lane 1's look A/B keys, `current` vs `warm-fill` vs `warm-fill + LUT`, same map, same frame, artist picks. Numeric side: a headless GDScript can evaluate the ProceduralSkyMaterial's colours analytically to get the ambient colour per preset, so the fill tint becomes a number rather than an adjective.

### T4. Rim / fresnel on characters (unit pop against busy ground)
- **Reference:** rim light fights the "plaster effect" and separates a character from the ground [SOURCED: Gamedeveloper "Character Rim Lighting"]. Animal Crossing NH-class Switch titles stack shadows on everything, PBR, AO and sky scattering [SOURCED: AC:NH breakdown summary]. [ASSUMPTION, observed: FE Engage units carry a light rim and outline on the map.]
- **Conquest:** units have no rim (rim only on the selection highlight, `UnitVisualManager.gd:323-325`). They use Burley/Lambert StandardMaterial while the world uses `diffuse_toon`, so the two sit under different lighting models. [CODE]
- **Verdict: GAP.**
- **Change:** in `unit_glow_import.gd`, set `rim_enabled`, `rim` ~0.3, `rim_tint` ~0.5 on the unit base StandardMaterial3D. Optionally set `diffuse_mode = DIFFUSE_TOON` for parity with the world. The full ramped-toon unit shader is already specced in `godot-import-notes.md §6` for cel units. Skins and dim overlays keep working because the material stays a StandardMaterial3D.
- **Mobile:** rim and toon diffuse are BaseMaterial3D features in the regular light pass. Supported, negligible cost.
- **Experiment:** A/B rim off vs on and Burley vs toon, with units standing on tall grass and on flagstone at default battle zoom.

### T5. Tilt-shift DOF (the diorama read)
- **Reference:** Link's Awakening (Switch) is built on tilt-shift blur at the screen edges to sell "real-life miniatures" [SOURCED: GamesRadar]. Octopath/HD-2D pairs DOF with bloom and vignette; DOF is core to the look and divisive enough that players ask how to turn it off [SOURCED: Steam threads; UE interviews].
- **Conquest:** none.
- **Verdict: IMPROVEMENT** (not a defect; a tactics board has to stay readable).
- **Change:** `CameraAttributesPractical` on the battle and overworld cameras. **Far blur only**, starting just past the board's far edge, so the skirt and horizon soften and no playable cell blurs. Focus distance follows the camera-to-cursor/active-unit distance, eased. Near blur off, or a very mild band below the board. Gate it behind a graphics-quality setting. `GameSettings` has no graphics tier yet.
- **Mobile:** DOF is supported in Forward+ and Mobile, not Compatibility [SOURCED: CameraAttributesPractical docs]. It is a full-screen blur, so it is the first thing to drop on low phones.
- **Experiment:** A/B off vs far-only on the largest board at max zoom-out, checking that the top row of cells stays sharp. [ASSUMPTION: settle "far edge" from the camera fit math in `CameraController.gd:268-277` per board size, not by eye.]

### T6. Fog: distance, height, atmosphere
- **Reference:** distance fog plus height fog gives depth layering at near-zero cost.
- **Conquest:** depth fog 70 to 240 plus the shader haze outside the board. The `.tscn` files carry height-fog values (`GameWorld.tscn:26-33`, `OverworldScene.tscn:17-24`) but `WorldLook` switches to `FOG_MODE_DEPTH` (`WorldLook.gd:241`). [ASSUMPTION: whether `fog_height_density` still applies in depth mode in 4.6. Settle it by reading `Environment` docs/source, or with one A/B.]
- **Verdict: ALIGNED** (haze already works through `world_lib`). Ground mist in the skirt's low spots is an **IMPROVEMENT**.
- **Change:** optional height fog, or a shader-side low-ground mist in `world_skirt.gdshader`, for dawn and swamp presets.
- **Mobile:** depth and height fog are supported. **Volumetric fog is Forward+ only**: do not build on it.

### T7. Ambient occlusion / GI at our scale
- **Reference:** AO plus a colourful bounce is the "soft, rich" read. Switch titles fake most of it (baked or vertex AO) [ASSUMPTION; consistent with DF noting Switch titles generating light/shadow maps].
- **Conquest:** SSAO on. Forward+ only, so the phone build loses it. Props already fake contact AO (`stylized_props.gdshader:61-63`). Forge units bake AO into COLOR_0.
- **Verdict: ALIGNED on desktop, GAP on mobile.**
- **Change:** adopt **renderer-independent AO** as the rule. AO lives in vertex colour or shader math (ProcMesh tiles, forge props/houses bake AO into COLOR like the units). SSAO is a desktop-only bonus on top. Fake bounce with a hemisphere term (T3). **LightmapGI is not viable** (tiles and props are generated at runtime per map). **SDFGI and VoxelGI are Forward+ only**, so they are rejected for parity. SSIL is listed as Forward+-only in the environment docs; treat it as desktop-only if used at all.
- **Mobile:** vertex AO and shader AO cost nothing extra.
- **Experiment:** a desktop A/B with SSAO off shows what the phone sees today. If the board goes flat, baked AO on tile caps and sides is worth a lane.

### T8. Shadows
- **Conquest:** 4 PSSM splits, 140 m, blur 1.6. [CODE `WorldLook.gd:199-204`]
- **Verdict: IMPROVEMENT** (desktop fine; the mobile profile is unset).
- **Change:** a `.mobile` profile: 2 splits, max distance fitted to the max zoom-out (the camera fit in `CameraController.gd:268-277`), shadow atlas 2048. [ASSUMPTION: that Godot's default `soft_shadow_filter_quality.mobile` is Hard, which would ignore blur 1.6 and give jagged shadow edges on phones. Settle it by reading the 4.6 project-settings docs.] Blob contact shadows under units already exist (`WorldLook.gd:292-317`). Keep them, since they are the mobile fallback.
- **Mobile:** directional shadows are supported. The pass count (splits) is the cost driver.

### T9. Wind, foliage sway, water, motes
- **Conquest:** foliage, tall grass and decor sway with a global wind multiplier. Stylized water with laps exists. Light motes exist. [CODE `stylized_foliage.gdshader:44-68`; `stylized_water.gdshader:73-114`; WORLD_ART.md]
- **Verdict: ALIGNED.** Already at the technique level of the references. No change proposed.

### T10. Cloud shadows
- **Reference:** slow-moving cloud shadow patches are a cheap "living world" layer, common in stylized outdoor games. [ASSUMPTION: no developer source found for FE/Pokemon specifically.]
- **Conquest:** none.
- **Verdict: IMPROVEMENT.**
- **Change:** Godot 4's DirectionalLight3D has no projector texture, so do it in `world_lib.gdshaderinc`. Add a `cloud_shadow(world_pos)` term: scrolling world-space fbm, multiplied into the lit portion. Every world shader already includes this file, so one function covers tiles, props and skirt. Drive it with new global uniforms (`cloud_amount`, `cloud_dir`) that weather can tween. Units would need the same term in their material to stay consistent; skip it first and check whether the mismatch is visible.
- **Mobile:** one fbm per fragment. The include already runs fbm, so it fits the budget on both renderers.
- **Experiment:** A/B on a large grass map. Watch readability: a shadow patch must not look like a terrain or hazard class.

### T11. Vignette / framing
- **Conquest:** vignette exists only in the rain overlay (`weather_overlay.gdshader`, `WeatherFX.gd:5`).
- **Verdict: IMPROVEMENT** (small).
- **Change:** a permanent subtle vignette in the existing weather overlay CanvasLayer (strength ~0.15-0.25, warm-dark tint to match the amber UI).
- **Mobile:** a 2D shader. Trivial.

### T12. Environment dressing density and silhouette (where "art level" mostly lives)
- **Reference:** modern Pokemon and FE world assets are *not* technically rich. DF found Scarlet/Violet's environments use "basic geometry and crudely placed textures" with low-res ground and wall art [SOURCED: DF via NintendoLife]. Three Houses drew criticism for low-res pavements and building textures [SOURCED: TheGamer]. What they have that Conquest's towns lack is **authored composition**: clustered clutter (barrels + crates + sacks), ground breakup at every wall foot (dirt ring, tufts, stones), varied rooflines, doors, windows and overhangs that give scale cues, and props that tell you what a building is.
- **Conquest:** identical house boxes, no doors or windows, no base dressing. [CODE audit §2]
- **Verdict: GAP.** This is the one the artist is actually seeing.
- **Change:** this is the **already-queued world-asset family** (counted separately below). Add **dressing kits** to its scope: a "clutter cluster" prop that places 3-7 small items by hash, and a "wall-foot breakup" strip auto-spawned around every building footprint. Both should be MultiMesh-batched so the draw-call count stays flat on mobile.
- **Mobile:** MultiMesh is supported on both renderers. Per-node draw calls are the real mobile risk. [ASSUMPTION: board tiles are one MeshInstance each, `LowPolyTileBuilder.gd`, so a 20x20 board means 400+ draws before decor. Settle it with Godot's draw-call monitor in a desktop playtest.]

### Two-column renderer support (techniques above)

| Technique | Forward+ (desktop) | Mobile (phones) |
|---|---|---|
| MSAA 3D | yes (2/4/8x) | yes; 2x recommended max |
| FXAA / SMAA | yes | yes |
| TAA / FSR2 | yes | **no** |
| Glow / bloom | yes, HDR to 16F | yes, **range capped at 2.0**, faster in 4.6 |
| Tonemap (Filmic/ACES/AgX) | yes | yes (AgX forced white 2.0) |
| Adjustments + 3D LUT | yes | yes |
| DOF (CameraAttributes) | yes | yes (cost: full-screen blur) |
| Depth + height fog | yes | yes |
| Volumetric fog | yes | **no** |
| SSAO | yes | **no** |
| SSIL | yes | **no** (env docs) |
| SDFGI / VoxelGI | yes | **no** |
| LightmapGI | yes | yes (not usable: runtime geometry) |
| SSR | yes | **no** |
| Sky ambient / ProceduralSky | yes | yes |
| Rim / toon diffuse (BaseMaterial3D) | yes | yes |
| Vertex-shader wind, custom fbm shaders | yes | yes |
| MultiMesh | yes | yes |
| Omni/Spot lights | 512 per cluster | **8 per mesh**, 256 per view |
| Framebuffer | RGBA16F | RGB10A2 (banding risk; use debanding) |

Sources: Godot renderers page, Environment & post-processing page, 3D AA page, CameraAttributesPractical class ref.

---

## 2. Asset level: how far the planned family closes the gap

- **Today:** houses ~200-400 tris, identical, no openings [audit §1b]. Units are forge house style with baked normal + AO.
- **Reference budgets:** no published numbers found for FE Engage, Three Houses or Pokemon building meshes. [ASSUMPTION: Switch-generation stylized buildings run roughly 1-5k tris with 256-1024 px textures. Settle it by having the artist open one FE Engage or Three Houses map building from a public model archive in Blender and read the stats (no download by a lane).]
- **What closes it:** forge-built house variants at **1-3k tris** (the audit's 600-1,200 is low for hero-town buildings). Use a **one-segment bevel on every outward edge** so toon light and rim catch the edges; this is the single biggest perceived-quality lever in flat-shaded low-poly. Add real door/window insets, eave overhangs, chimneys and timber trims. Bake AO and a top-light gradient into vertex colour (renderer-independent, T7). Expose the tint parameter per the audit's §4 constraint. Add the dressing kits from T12. A 30-building town at 3k tris is ~90k tris, well inside a phone budget. Draw calls matter more than tris.
- **What T1-T11 add on top:** clean edges (AA), coloured shadows and warmth (T3), emissive punch (T2), unit separation (T4), diorama depth (T5) and life (T10). These change every frame the player sees, including the battle board, which the family does not touch.

---

## 3. Ranked top-6 "feel" improvements (world-asset family excluded; already queued)

| # | Lane (one deliverable each) | Files | Impact | Effort | Mobile |
|---|---|---|---|---|---|
| 1 | **AA + render profile:** MSAA 4x desktop, `.mobile` 2x or SMAA, debanding, mobile shadow profile (2 splits, fitted distance) + a dev **look A/B key** that cycles saved WorldLook presets (this is what makes every experiment here one artist launch) | `project.godot`, `WorldLook.gd` (A/B hook) | High: every edge on screen | S | designed for it |
| 2 | **Glow retune:** SCREEN blend, 4.6 levels, threshold ~1.0-1.2, re-derive weather glow scales; record the forge "emissive peak ≤ 2.0" palette rule | `WorldLook.gd:229-233`, `WeatherEnvAdapter` params | High on glowing units, fire, magic | S | fits the 0-2 range |
| 3 | **Warm grade pass:** re-tinted fill (sky/ground hemisphere + `ambient_light_color`), key:fill toward ~3:1, tonemap white A/B, per-preset 3D LUT support (forge generates the LUTs) | `WorldLook.gd:18-52, 120-139, 217-237` | High: the "FE warmth" read on board + overworld | M | yes |
| 4 | **Unit rim + toon parity:** rim on unit base materials, optional `DIFFUSE_TOON` | `unit_glow_import.gd` | Med-High: units pop off busy ground | S | yes |
| 5 | **Cloud shadows + hemisphere bounce** in the shared world include | `world_lib.gdshaderinc` (+ globals in `project.godot`) | Medium: "living world", coloured shadows on mobile | M | yes, renderer-independent |
| 6 | **Tilt-shift far DOF + permanent vignette**, behind a new graphics-quality setting | battle/overworld camera setup, `GameSettings`, weather overlay | Medium: diorama read | M | yes; first to disable on low phones |

Not ranked (already ALIGNED): wind/sway, water, motes, far haze. Rejected for parity: volumetric fog, SDFGI/VoxelGI, SSR, lightmaps.

**World-asset family (separate, queued):** house/cabin variants at 1-3k tris with bevels, openings, trims and vertex-baked AO; chapel/smithy; clutter-cluster and wall-foot dressing kits (MultiMesh); NPC archetypes. Order per the audit.

---

## 4. Honest answer to the artist

Yes, the *feel* is reachable, and most of the gap is not model quality. Modern Pokemon and FE world assets are technically modest: Digital Foundry called Scarlet/Violet's environments "basic geometry" with crude textures, and Three Houses was criticized for low-res buildings and pavements. What makes them feel modern is clean edges, warm and coloured light with saturated shadows, glow that sells magic, characters that pop off the ground, a diorama camera, and authored clutter around every building. Conquest's foundations are already right (one look owner, painterly world-space shaders, wind, water, haze). It has no anti-aliasing, a glow mode that suppresses bloom, and grey-blue fill light. Those are settings and shader work, not new art, and lanes 1-4 are small. The world-asset family (bevelled, dressed, varied houses) closes the rest of what the artist sees in towns. The real ceiling is not the GPU. Some things stay out of reach on the shared phone build: dynamic GI with true coloured bounce, volumetric god-rays and fog, SSR water, and HDR bloom past 2.0. Those can be desktop-only extras, at the cost of the phone build looking different. The other ceiling is labor: FE Engage's authored battle cutscenes and hand-composed set dressing are man-years of animation and level art. We can match the board and town *feel* at a phone budget. We will not match a 100-person studio's volume of bespoke content without scoping to fewer, more authored set-pieces.

---

## Sources
- Godot docs: [Renderers comparison](https://docs.godotengine.org/en/stable/tutorials/rendering/renderers.html), [Environment and post-processing](https://docs.godotengine.org/en/stable/tutorials/3d/environment_and_post_processing.html), [3D antialiasing](https://docs.godotengine.org/en/stable/tutorials/3d/3d_antialiasing.html), [CameraAttributesPractical](https://docs.godotengine.org/en/stable/classes/class_cameraattributespractical.html)
- Godot 4.6: [release notes](https://godotengine.org/releases/4.6/), [PR #110671 glow before tonemap, Screen default](https://github.com/godotengine/godot/pull/110671), [PR #106940 AgX white/contrast](https://github.com/godotengine/godot/pull/106940), [Issue #101558 AgX on Mobile, 0-2 range](https://github.com/godotengine/godot/issues/101558)
- [Godot forum: low-poly light/environment setup](https://testing.godotforums.org/discussion/21876/light-and-environment-setup-for-3d-low-poly-style-game)
- Digital Foundry: [Legends: Arceus breakdown (NintendoWire)](https://nintendowire.com/news/2022/02/08/digital-foundry-analyzes-pokemon-legends-arceus-graphics-in-new-breakdown/), [Scarlet/Violet analysis (NintendoLife)](https://www.nintendolife.com/news/2022/11/video-digital-foundrys-technical-analysis-of-pokemon-scarlet-and-violet)
- [TheGamer: Three Houses visuals](https://www.thegamer.com/fire-emblem-three-houses-replay-graphics-performance/); [Nintendo Ask the Developer vol. 8, FE Engage](https://www.nintendo.com/us/whatsnew/ask-the-developer-vol-8-fire-emblem-engage-part-2/)
- HD-2D: [UE interview Octopath II](https://www.unrealengine.com/en-US/developer-interviews/octopath-traveler-ii-builds-a-bigger-bolder-world-in-its-stunning-hd-2d-style), [UE spotlight Octopath](https://www.unrealengine.com/spotlights/octopath-traveler-s-hd-2d-art-style-and-story-make-for-a-jrpg-dream-come-true), [Steam DOF thread](https://steamcommunity.com/app/921570/discussions/0/1640913421077220954/), [HD-2D post grade recreation (DOF/bloom/vignette)](https://github.com/wolfhound0376/v0-ashes-of-prometheus/pull/434)
- [GamesRadar: Link's Awakening tilt-shift blur](https://www.gamesradar.com/a-links-awakening-mod-removes-that-blur-effect-around-the-screen/)
- [Gamedeveloper: Character rim lighting](https://www.gamedeveloper.com/programming/character-rim-lighting)
