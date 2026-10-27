# League of Legends + fan-skin modelling: lessons for our pipeline (research lane, 2026-10-26)

Artist (verbatim): "since people make custom skins for league of legends, we should take a look at league models and
fan models for skins to learn and improve our modeling have an agent look into it".

How it was done: web research only, with no downloads.

Labels:
- [FACT] = a Riot source, or a primary source quoting a Riot artist.
- [COMMUNITY] = forum / fan practice.
- [INFERENCE] = unverified reasoning.

Access limits: Polycount returned 403 (only search snippets were read), old Nexus posts redirect, and the Riot artist
talks are video / paid.

## Key facts

**Budgets**
- [FACT] Little Demon Tristana was retopologised to ~7-10k tris (Upcomer, summarising Riot Nexus).
- [FACT] High Noon Senna's body is ~13k polys. Workflow: ZModeler sculpt -> retopo / UV -> bake -> paintover (Sketchfab
  "Art Spotlight: High Noon Senna").
- [FACT] Riot's contest rules: ONE 2048 diffuse sheet, flat / full-bright, with AO, coloured light and GI baked or
  painted in. No normal or spec maps. Efficient UVs and topology are judged (Polycount, Riot art contest 2014 / 2017).

**Lighting and colour**
- [FACT] Champions are not lit by the scene. Riot bakes a roughly top-down light and paints over it; the hand-painted
  look comes from that.
- [FACT] Katia Bourykina's method: clean bakes -> build the lighting -> paint in greyscale -> map greyscale to colour
  gradients.

**Value hierarchy** ([FACT], Riot 2014 SR texture rebalance)
- Particles over characters over the environment.
- Highlights on heads and weapons.
- Lower value on unimportant areas.
- Cut flicker and noise.

**Readability**
- [FACT] "Clarity in League":
  - the silhouette first (the darkness test);
  - one unique primary feature kept on every skin;
  - facing direction readable from the silhouette alone;
  - minimal noise;
  - no near-permanent glows.
- [FACT] Ahri's ASU: fine detail on a small element goes muddy at camera height. Test from several angles.

**Faces**
- [FACT] Teemo's ASU added face density for expression, and the face rig was tested on proxies.
- [FACT] Riot reuses heads and base meshes across skins. Faces can be toggled submeshes.
- [INFERENCE] League faces have shallow modelled sockets and lids. The iris, brows and lips are PAINTED; readability
  comes from painted value contrast, not carved depth.
- [COMMUNITY] Modelled anime eyes bulge or break in profile. Painted or decal eyes (UV-swapped for expressions) are
  common.

**Hair**
- [COMMUNITY] Stylized game hair is chunky SOLID clumps of varied size. Alpha cards are for edges / flyaways only, and
  fused hair is rare. The usual hybrid: a volume shell -> mid clumps that do not intersect -> a few flyaways.
- [COMMUNITY] Painting: highlight the top of each chunk, darker edges, roots dark -> tips light. A gradient alone reads
  shapeless, and uniform "stringy" strand textures look bad.

**Secondary motion**
- [FACT] Little Devil Teemo's tail is a jiggle chain of ~6 joints with delay + dampening.
- [FACT] Ahri's tails run live simulation, with values varied per tail and a master / individual blend.

**Fan tooling** ([FACT], tool docs)
- lol2gltf, LtMAO (lemon3d, wad_tool, ritobin), CS-LoL Manager / .fantome mods.
- Fan mods break League's ToS; Riot disqualifies contest entries built from ripped models.
- Classic export mistakes: untriangulated faces, flipped normals, missing UVs, unweighted vertices.
- [INFERENCE] Good fan skins keep the base silhouette and the primary feature, match the flat-lit painted look (PBR
  ports look wrong), and have clean shoulder / hip weights.

## Recommendations mapped to our pipeline (most valuable first)

Tags:
- FITS = automatable in the AI + light-finish flow.
- PROC = a new procedural tool.
- HAND = authored once.

1. **[FITS / PROC] Split the generator mesh by region before the remesh** (body / head-face / hair). Voxel-remesh and
   decimate the body only. The generator's hair and face become shape / colour references. This is the root cause of
   the hair ropes and dented faces.
2. **[PROC] Rebuild the texture League-style** instead of baking the generator's texture as is:
   - de-noise at one game pixel, then snap each region to the sheet palette;
   - re-light: a top-down gradient x AO from the clean low (ao_from_low) + a soft upper rim;
   - value hierarchy: head / face / weapon brightest, legs / feet darker; clamp the extremes;
   - gate at the real on-screen size (~80-150 px tall, with mipmaps).
3. **[PROC + HAND template] Faces: a smooth modelled head + PAINTED features.**
   - Shallow sockets; eyes, brows, lashes and lips as texture or a thin decal mesh with its own UV island.
   - Big readable eyes come from contrast: a dark lash line, a light white, an iris with a highlight dot.
   - Face / hair texel density 1.5-2x the body's.
4. **[PROC] Hair as separate SOLID pieces from curves.**
   - A scalp cap + primary locks: flattened, tapered bevels, ~6-10 segments along x 3-5 around.
   - Tier units 5-10 locks; heroes 12-25 locks in 2-3 layers.
   - Roots buried in the cap. ~15-25 % of a hero's tris.
5. **[PROC] Procedural lock painting** along root -> tip UVs: a dark root -> light tip gradient, 1-2 highlight bands on
   the top faces, darker edges / contact shadows, a few strokes per lock.
6. **[HAND rule] Features drawn over hair on 2D sheets:** restyle the bangs to stop above the brows (Riot: "fix what
   doesn't work in 3D"). Brows over bangs only via render priority / stencil; check Godot 4.6 support.
7. **[PROC] Spring-bone chains** of 3-6 bones per lock group / cape panel / tail, driven in Godot by
   SpringBoneSimulator3D, with stiffness varied per chain.
8. **[FITS] A readability gate at the game camera:** silhouette, greyscale value and facing-direction renders; flag a
   primary feature that does not read.
9. **[FITS] Budgets:** our 8-22k tiers already meet League's 7-13k; our heroes are ~3x. Spend tris on the silhouette /
   face / hair. 1024 texture for tier units, 2048 for heroes; diffuse + emission only.
10. **[HAND once] A head / base-mesh / decal library:** clean symmetric rig-ready heads (humanoid, anime, beast) with
    eye / mouth decal sets and lock presets, fitted to the generator's proportions.
11. **[FITS] Small, focused glow** (eyes, cores, weapon) so VFX can sit on top.
12. **[FITS] Hair / helmets / accessories as named submeshes** that can be shown or hidden.

## Orchestrator note: what we already have

The procedural hand-build pipeline already has a separate-lock hair system:
- improve/{wren,elias,varden,lyra}_s5_hair.py, ~1.2-1.8k lines each;
- a dark inner scalp cap; ribbon locks (ribbon_plan / build_lock) with layer resolve;
- painted tiers (hair_shade / hair_root / hair_ring / hair_tip).
It matches recommendations 4-5 closely, and the earlier gap analysis design/research/hair-face-best-practices.md
(2026-10-03) audited it. The light flow can reuse it for the hair pass instead of building from scratch.

## Main sources

- https://upcomer.com/an-early-look-at-little-demon-tristana-and-the-lol-model-creation-process
- https://sketchfab.com/blogs/community/art-spotlight-high-noon-senna
- https://polycount.com/discussion/160143/riot-games-art-contest-2014
- https://www.leagueoflegends.com/en-us/news/dev/clarity-in-league/
- https://www.leagueoflegends.com/en-us/news/dev/dev-ahri-s-asu/
- https://www.leagueoflegends.com/en-us/news/dev/dev-scouting-out-teemo/
- https://www.gamedeveloper.com/design/rigging-nightmares-tech-art-meets-the-devil
- https://www.riotgames.com/en/news/elementalist-lux-10-skins-30-megabytes
- https://www.surrenderat20.net/2014/09/red-post-collection-417-live-gameplay.html
- https://support.steampowered.com/kb/8700-SJKN-4322/dota-2-character-texture-guide
- https://github.com/Crauzer/lol2gltf
- https://docs.modelviewer.lol/file-formats
- https://docs.godotengine.org/en/latest/classes/class_springbonesimulator3d.html
