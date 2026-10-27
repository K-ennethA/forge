# Marvel Rivals + close-camera stylised characters: lessons for a Dungeon Diver master pipeline (research lane, 2026-10-26)

Artist (verbatim):
- "have another agent look at marvel rivals since our game is not top down pov"
- "for conquest it makes sense to optimize and paint there or lower quality but for this game it seems higher quality is better"
- "all of our characters we assume both and lower quality as needed"
- "always highest option first"

How it was done: web research only.

Labels:
- [FACT] = the developer or a primary source.
- [COMMUNITY] = forums, fan recreations.
- [INFERENCE] = unverified reasoning.

Limits: Marvel Rivals publishes no talk or budget numbers, and GDC Vault / Polycount / the UE interview did not load.
The numbers below therefore come from Overwatch, Guilty Gear Xrd and League. Companion pass:
design/research/league-modeling-2026-10-26.md.

## Findings

**Marvel Rivals**
- [FACT] The style is "Dynamic Comic": Western comics + manhua / anime, bold saturated colour, rough brushstrokes, flat
  stylised light. The art director credits a "2D mindset" in the concept-to-3D step.
- [FACT] The shader is a custom NPR + PBR hybrid: artists control the colour tendency of shadows and the highlight
  shapes; direct and ambient light are handled separately per character, with local exposure; Lumen is tuned for anime.
- [FACT] Textures keep the colour information while showing fine detail.
- [FACT] Every costume keeps the silhouette, logo, signature weapon and colour scheme.
- [FACT] The rig is standard, with extra care around the eyes.
- [COMMUNITY] Fan recreations find flat colour PATCHES with line-like transitions instead of gradients ("almost never
  smoothed"), hair cards over a base mesh, and outlines faked with an inverted hull. Players complain about blunt-cut
  hair and effects clutter.
- [INFERENCE] The look is roughly 50 % texture design, 35 % shader, 15 % geometry. The outlines are thin and secondary.

**Faces**
- [FACT] Guilty Gear Xrd (GDC 2015):
  - ~40k tris, ~500 bones;
  - face normals edited by hand;
  - vertex-colour light threshold, vertex-painted shadow areas;
  - no self-shadow on hair / coats;
  - a light rig per character;
  - textures 1 x 2K + 2 x 1K (a lit base + a shadow tint);
  - mesh shapes deformed per keyframe.
- [FACT] Overwatch: 200-225 bones, ~70 of them in the face; one shared 256 eye texture set; a fake-subsurface "blood
  map". Shin Megami Tensei V: a face shadow mask + normal transfer.
- [COMMUNITY] The three face-shadow methods: normal transfer from a sphere / smoothed proxy; SDF face shadow maps
  (Genshin; a Godot 4 MIT shader exists); hand-painted masks per light angle.
- [INFERENCE] Without a toon shader (the artist rejected cel + outlines on vampwarrior v3), normal transfer from a
  smoothed head still removes "dented" shading. A close camera (faces 60-100 px in play, full screen in close-ups)
  needs real eye geometry and ~2x face texel density. Carved slit eyes fail.

**Hair**
- [COMMUNITY] Genshin: solid hair, the anime highlight painted into the texture, little self-shadow.
- [COMMUNITY] Overwatch: anisotropic hair imitated with a painted flow map. SMT V: an anisotropic "angel ring".
- Rules: anisotropy per tuft, not per head; cards only for flyaways / edges; a solid volume layer under everything.
- [INFERENCE] What a close camera changes from League:
  - more layers (inner volume -> mid -> tapered tips) and more segments along each lock;
  - optional ribbon / alpha tips at the bang / ponytail ends;
  - a painted contact shadow on the forehead under the bangs;
  - long hair on 3-5 spring chains with collision capsules;
  - face-covering bangs split into separate locks over the eyes.

**Readability**
- [FACT] Overwatch checks every new silhouette against the existing heroes.
- [FACT] Valorant: angular forms, clean simple gradients, the role readable from the look, hands holding weapons
  enlarged for legibility. Fidelity goes up only after the readability / performance targets are met.
- [FACT] TF2 (Valve, NPAR 2007): rim light + luminance / hue variation carry shape and team; a warm terminator.

**Numbers** ([FACT])

| Reference | Tris | Textures | Rig / other |
|---|---|---|---|
| Overwatch | Hero LOD0 ~30k (33-34k tolerated) + weapon 15k; 4 LODs at x0.5 each | 2048 set (colour+AO, normal, spec+rough, emissive 512-1024); <30 MB per hero | <=256 bones, 4 influences |
| Guilty Gear Xrd | ~40k | 2K + 2 x 1K, point-sampled | ~500 bones; ~2 months modelling + 2 months animation per character |
| League | 7-13k | One 2048 diffuse with the light painted in | — |

## The pipeline this implies: one high-quality master (Dungeon Diver) + one derive-down step (Conquest)

Tags:
- FITS = automatable in the AI + light-finish flow.
- PROC = a new procedural tool.
- HAND = authored once.
- "no toon" = works with our flat / standard-lit look.

### (a) Dungeon Diver master: budgets ([INFERENCE], anchored on Overwatch / Guilty Gear / League)

| Item | Hero | Creature (tier) | Boss |
|---|---|---|---|
| Tris, LOD0 | 35-50k (incl. hair) + weapon 5-10k | 12-25k | <=50k |
| Hero share: head | 6-9k (face 3-5k) | — | — |
| Hero share: hair | 6-12k | — | — |
| Hero share: hands | 2-3k | — | — |
| LODs | 50 / 25 / 12 % | Same | Same |
| Body texture | 2048: albedo + normal (medium forms only) + ORM | One 2048 set, or 2048 body + 1024 head | — |
| Head texture | 1024-2048 at ~2x the body's texel density | (in the 1024 head set) | — |
| Hair texture | 1024 | — | — |
| Shared sets | 512 eye set; emission 512-1024 | — | — |
| Rig | ~100-150 bones, ~20-40 of them face bones / blend shapes | — | — |
| Gate | Readability at ~400-650 px body height + a face close-up | — | — |

### (a) Dungeon Diver master: changes, most valuable first

1. **Face from a clean head** [PROC + HAND template, no toon]
   - Normal transfer from a smoothed / ellipsoid proxy (removes the dented shading).
   - Separate eye meshes (a recessed disc / eyeball with an iris and highlight).
   - Painted brow / lash / lip decal geometry with its own UV island.
   - Edge loops around the eyes / mouth for blend shapes.
2. **Texture as colour patches** [PROC, no toon]
   - De-noise the generator bake, snap it to the sheet palette as flat patches with crisp transitions.
   - Painted value design (lighter head / torso, darker toward the feet) + painted stylised highlight shapes.
3. **Hair as layered solid locks** [PROC, reusing the s5_hair ribbon system, no toon]
   - Inner dark volume -> mid locks -> tapered tips, 8-14 segments along each lock for heroes.
   - Forehead contact shadow; the scalp in the hair colour at the hairline.
   - A root -> tip gradient + an angel-ring band per lock; an optional anisotropic flow map.
4. **Secondary motion** [PROC, no toon]: SpringBoneSimulator3D chains of 3-6 bones per lock group, 3-5 chains for long
   hair with head / shoulder / chest collision capsules, capes / tails on their own chains, stiffness varied per chain.
5. **Features over hair** [HAND rule + PROC]: restyle the bangs above the brows, or draw brows / eyes after the hair
   (Godot 4.5+ stencil / render_priority); split face-covering bangs.
6. **A character light rig instead of a toon shader** [FITS, no toon]: a key + rim light per character (Godot cull
   masks), an optional slight cool shadow tint (not cel banding), capsule / contact AO.
7. **A readability gate at the DD camera** [FITS]: a silhouette, a value check, the primary feature at 400-650 px,
   slightly enlarged hands / weapon. Any enemy outline is gameplay-only (a stencil), not part of the character look.
8. **Cheap expressions** [HAND per head template]: blink, 3-4 visemes, brow blend shapes; optional eye UV swaps.

### (b) Deriving the Conquest version

9. **The derive step** [FITS]
   - Start from DD LOD1 / LOD2: Conquest tier units 6-12k tris, heroes 12-22k.
   - Bake the lit look into ONE diffuse League-style: the light rig + AO + rim + value hierarchy, baked "top-down",
     brows / eyes baked in.
   - Textures: 1024 tier / 2048 hero, diffuse + emission only, no normal map.
   - Merge the hair locks (outer silhouette locks only) and keep 1-2 spring chains.
   - Drop the blend shapes except blink. Fold the decal eyes into the head.
   - Gate at the board camera, ~80-150 px.
10. **Paint for distance** [PROC]: push the value contrast and drop small patches at 1024 / ~100 px.

### (c) Shared by both

11. **The same master files:**
    - one rig (Conquest uses a subset of the bones);
    - one palette / colour map;
    - named, toggleable submeshes (hair, helmet, accessories, decals);
    - a small, focused emission mask.
12. **A head / eye / hair template library** [HAND once]: clean symmetric heads (humanoid / anime / beast) with eye /
    mouth decal sets, normal-transfer proxies and lock presets.

### Versus the League pass

- **Stays:** separate head / body / hair; solid locks on a cap; painted features; a value hierarchy; small focused
  glow; spring bones; submeshes; one baked diffuse, now only in the Conquest derive.
- **Changes:** eye geometry + normal transfer + blend shapes; more hair layers / taper / forehead shadows; colour
  patches instead of gradients; ~3-4x the tris + a normal / ORM set in the master.
- **New:** a character light rig instead of toon shading; brows over hair via stencil; the derive step; enlarged
  hands / weapons.
- **Depends on toon tech the artist rejected:** Marvel Rivals' outlines, its NPR shadow / highlight shaping and SDF
  face shadows. What transfers without them: texture design, the costume consistency rules, per-character lighting and
  simple exaggerated shapes.

## Main sources

- 80.lv, Marvel Rivals character art pipeline: https://80.lv/articles/inside-marvel-rivals-character-art-vfx-destruction-pipeline
- CreativeBloq, the art director on the "2D mindset": https://www.creativebloq.com/3d/video-game-design/the-real-key-to-our-success-was-keeping-a-2d-mindset-marvel-rivals-art-director-on-the-games-unique-comic-book-style
- The Rookies, a study of the Marvel Rivals style: https://www.therookies.co/entries/41175
- GosuGamers, Overwatch tech-art budgets: https://www.gosugamers.net/overwatch/news/35188-interesting-insight-into-overwatch-tech-art
- GDC 2015, Guilty Gear Xrd: https://gdcvault.com/play/1022031/GuiltyGearXrd-s-Art-Style-The
- BlenderNation, Guilty Gear Xrd summary: https://www.blendernation.com/2015/07/26/junya-c-motomura-behind-the-scenes-of-guilty-gear-xrd/
- Epic Games Japan, Stylized Rendering Insights: https://docswell.com/s/EpicGamesJapan/5DEVPV-2023-12-01-082936
- Godot shaders, anime face shader: https://godotshaders.com/shader/anime-style-face-shader/
- Inverse, Valorant art style interview: https://inverse.com/gaming/valorant-art-style-interview-moby-francke
- Valve, Illustrative Rendering in TF2: https://www.cs.princeton.edu/courses/archive/fall07/cos597B/papers/mitchell-team-fortress.pdf
- Godot 4.5 release: https://godotengine.org/releases/4.5/
- Godot docs, SpringBoneSimulator3D: https://docs.godotengine.org/en/latest/classes/class_springbonesimulator3d.html
