# Hair + face pipeline vs real-time stylized best practice: gap analysis (research lane, 2026-10-03)

Baseline audited: design/character-style-guide.md, improve/{wren,elias,varden}_STATE.md, the build knobs
(improve/elias_build.py, varden_build.py), the build reports (improved/elias.json, varden.json, *_hairdiag.json,
*_face_probe.json) and stills: renders/elias/elias_v3_{portrait,face_tq,hair_compare}.png,
renders/varden/varden_v1_{portrait,face_tq}.png, renders/wren/wren_v7_{portrait,lockids_hair_close}.png,
design/reference/fe-style/fe-archer-figure-hair.webp.

Labels: **[MEASURED]** = a number from our own reports. **[SOURCE]** = stated by a cited source.
**[ASSUMPTION]** = unverified; each one names the experiment that settles it. A source marked
*(search summary)* was read only through a search-result summary, not opened.

Constraint check applied to every proposal: deterministic headless Blender, palette-by-vertex-color, no hand
texture painting, Godot 4.6, hero tier 30-50k. Artist history this has to respect: cel shading + outline shells were
tried on vampwarrior and REJECTED (review-log, vampwarrior v3). Nothing below brings back a toon ramp or outlines.

## Measured facts used throughout

| Fact | Value | Where |
|---|---|---|
| Hair proxy normals carried by a tangent-space map on flat facets: share of samples the map CAN represent | 72.5 % | elias.json `hair_proxy_bake.representable_pct` |
| Decoded vs intended proxy normal, representable samples p50/p90/p99 | 16.4 / 44.9 / 107.4 deg | same, `decode_vs_proxy_deg` |
| Same, all samples | 25.2 / 59.2 / 142.8 deg | same |
| Report's own note | "a tangent-space map can only turn a facet within its own hemisphere" | same |
| Hair texel density (normal map) | 696,320 texels / 5,702 cm² of lock area, about 1.1 texel/mm (face 1.81, rest 0.40 texel/mm) | elias.json + elias_v3_hairdiag.json |
| Tris spent on facial hair vs scalp hair | Elias 6,358 beard vs 6,048 scalp; Varden (SHORT crop) 5,794 beard vs 4,608 scalp | *.json `tris` |
| Budget headroom | Elias 49.8k of 50k (0.2k left) | elias_STATE |
| Lock crossings past roots | Elias 61 pairs / 674 tri; Varden 46 / 416; Varden lock-pairs-cutting 73 / 2,333 tri pairs | STATE + varden_v1_hairdiag.json |
| Top-facet kinks (Varden) | p50 17.4 deg, p90 95.7 deg, 44.9 % of edges over 20 deg | varden_v1_hairdiag.json |
| Under-eye flatness | Elias 5.5 mm with painted age lines, 1.3-2.4 mm without (age-macro lids); Varden max 2.36 mm, dense-crease spike 5.28 mm at dx +19.5 mm (the outer-corner / crow's-feet zone) | elias_STATE, varden_v1_face_probe.json |
| Iris coverage | Wren 58 %, Elias 56.6 %, Varden 61.6 % | STATEs |
| Age macro | Elias MACRO age 0.80 (MPFB realistic ageing morph) | elias_build.py:42 |

What the stills show: (1) every lock on Elias and Varden has a checkered, per-facet shading pattern (scalp and beard);
(2) Elias's beard reads as about 25 shingled planks, and his top hair reads as an evenly spaced row of spike tips;
(3) Varden's short beard reads as vertical tubes hanging off a dark painted patch, with skin showing through at the
chin centre; (4) Varden's forehead and age lines read as thin scratches; (5) Wren's fringe shadow on the forehead
has a stair-stepped edge (wren_v7_lockids_hair_close.png).

---

## HAIR

### H1. Shape hierarchy and silhouette: **GAP**
- **Best practice.** Primary > secondary > tertiary shapes. Stylized work keeps the primary and secondary forms and
  removes most tertiary detail. Hair is blocked out as big clumps that fill the silhouette first, then refined
  ([80.lv, stylized character texturing/animating](https://80.lv/articles/the-allure-texturing-and-animating-stylized-characters)
  *(search summary)*). Blizzard character artist Natacha Nielsen: shapes "need to read well even at a distance", and
  detail everywhere makes things "muddy and you lose your focal point"
  ([80.lv](https://80.lv/articles/004adk-talking-about-stylized-character-art)). Anime hair is drawn "big clumps first"
  ([Clip Studio](https://www.clipstudio.net/how-to-draw/archives/171638) *(search summary)*). Our own style-bar figure
  (fe-archer-figure-hair.webp) shows a few large sculpted masses with grooves cut into them. It is not a field of
  equal ribbons.
- **What we do.** 25-66 independent ribbon locks radiating from one whorl. Widths come from HAIR_KIND_W 0.8-1.3, so
  the widest lock is at most about 1.6x the narrowest. Tips spawn per lock, which gives the evenly spaced spike row
  across Elias's crown.
- **Change.** Add a mass-first stage. A new knob HAIR_MASSES (4-7 primary masses per hairdo, each with its own flow
  curve and silhouette envelope) runs first. The existing ribbon locks then spawn INSIDE each mass as its secondary
  subdivisions, with a size spread of at least 3:1 (large to small). Tip lengths and spacing are jittered within a
  mass by a deterministic hash, so tips cluster into groups instead of a regular row.
- **Settling experiment.** On Elias v3, build it both ways. Measure (a) the coefficient of variation of silhouette
  tip spacing along the hairline and crown, and (b) the lock-width spread. Render portrait + 3/4 + one panel at
  gameplay pixel size, then get the artist A/B. [ASSUMPTION] that a mass-first hierarchy reads closer to the archer
  figure than the "segmented clean ribbons" Wren v7 direction. Both readings are in the review-log, so the artist
  decides.

### H2. Layering and interpenetration (the "chopped" look): **IMPROVEMENT**
- **Best practice.** Production stylized hair is built in several passes, draped layer over layer (Greg Mourino, Blue
  Sky, on Johan Lithvall's course: [80.lv](https://80.lv/articles/005cg-cgma-student-project-hair-for-games)).
  Sculpt workflows keep each clump separate and clean ("volume clumps" kept as their own subtools,
  [polycount search summary](https://polycount.com/discussion/236231/finished-stylized-character-kid)). Opaque
  shells that intersect are normal practice. The defect is a ragged intersection line, not the intersection itself.
- **What we do.** Layer resolve (merged order, lift/tuck rounds) + tuck shade. It is ALIGNED in intent, but
  residual crossings remain [MEASURED: 46-73 lock pairs], and Varden shows facet kinks at p90 95.7 deg.
- **Change.** After layer resolve, run a boolean UNION of every lock in a hair group (scalp / beard) into one shell,
  using Blender's Manifold solver. Each crossing becomes one clean crease line, which reads as a lock boundary
  (the "defined edge" the artist asked for). Buried faces are deleted, which frees tris and helps Elias at 0.2k
  headroom. Tuck shade can then key off distance to the crease curves.
- **Settling experiment.** Union Elias v3 + Varden v1 as a post-pass. Hair diag crossings should go to 0 by
  construction. Report the tri delta, sliver count, a two-run digest (determinism twin) and a portrait A/B.
  [ASSUMPTION] that the Manifold solver (Blender 4.5+) is deterministic and accepts our locks: the sharp tips and
  6-vertex sections must be watertight, so tips may need capping. [ASSUMPTION] that crease lines read cleaner than
  the current crossings on flat facets.

### H3. Root-to-tip flow, part/whorl, follow-through: **ALIGNED**
- **Best practice.** Plan growth direction first ([Sosa, 80.lv](https://80.lv/articles/making-hair-and-beards-for-aaa-games)).
  Flow comes from a part or whorl, and secondary motion goes on chains.
- **What we do.** Whorl/part flow (HAIR_WHORL), spines with a bend limit, follow-through chains on fringe/sides/back
  and beard. No change.

### H4. Hair colour tiers, tuck shade, angel ring (paint carrier): **GAP**
- **Best practice.** Stylized hair gets its root-to-tip gradient and highlight band from UVs laid out ALONG the strand
  (root at the bottom of a texture strip, tip at the top), so one gradient/highlight strip serves every lock
  ([Blender Artists, anime hair highlight](https://blenderartists.org/t/how-to-achieve-anime-hair-highlight/636129),
  [polycount](https://polycount.com/discussion/160776/how-should-i-texture-characters-hair), both *(search summary)*).
  Guilty Gear Xrd puts shading data in vertex colours because they "interpolate linearly... resolution independent"
  ([Motomura, GDC 2015](https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf)).
- **What we do.** Region colour is constant per face (region_id per polygon), applied by whole segments. Wren's
  STATE carries "paint tiers read blocky on locks" as a known residual, and the Elias/Varden stills show the same
  stepped bands.
- **Change.** Lay each lock's UV out lengthwise (u across the lock, v from root to tip, normalised arc length) into
  one shared, procedurally generated GREYSCALE hair ramp strip: root shade, mid, tip, and the angel-ring band at a v
  set per lock from elevation. The game multiplies it by the palette vertex tint, so palette swaps stay free. Tuck
  shade becomes a per-corner COLOR_0 gradient instead of a per-face cut. Side benefit: Mikktspace tangents then run
  along the strand, which Godot's anisotropy needs ([Godot StandardMaterial3D](https://docs.godotengine.org/en/stable/tutorials/3d/standard_material_3d.html):
  anisotropy "aligns it to tangent space... commonly used with hair").
- **Settling experiment.** Rebuild Wren v7 (approved, with a known residual). Metric: render-space luminance step
  across segment boundaries inside one lock (p90), before and after. Then the artist A/B. [ASSUMPTION] that a smooth
  gradient does not cost the "segmented, clean" read the artist approved. That is a taste question.

### H5. Normal direction trick for hair (proxy normals): technique **ALIGNED**, carrier **GAP**
- **Best practice.** Transferring normals from a sphere/proxy is standard for stylized hair and foliage
  ([Blender Data Transfer manual](https://docs.blender.org/manual/en/latest/modeling/modifiers/modify/data_transfer.html),
  [Yarsa DevBlog](https://blog.yarsalabs.com/normal-transfer-in-blender/)). Anime CG pros edit the normals of the
  face and hair through a proxy and transfer them back ([Xelabo](https://xelabo.tumblr.com/post/144049889178/mini-tutorial-normal-editing-for-3dcg-anime)).
  Guilty Gear Xrd stores this in VERTEX normals and explicitly avoids normal maps because texture data is "very
  resolution dependent" ([Motomura, GDC 2015](https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf)). ASW goes as
  far as a second normal set kept in the tangent channel
  ([ASW Academy toon-line talk](https://www.docswell.com/s/ASW_Academy/5LVY67-GG-Toonline-Eng)).
- **What we do.** The proxy egg is baked into a tangent-space normal map, against FLAT facets, at about 1.1 texel/mm.
  [MEASURED] 27.5 % of samples are unrepresentable, the decode error is p90 44.9 deg / p99 107 deg on the
  representable samples, and the residual neighbour step is p90 9.6 deg. This matches the checkered facets in every
  hair still.
- **Change.** Write the proxy normals (leaned 45 % per lock, as now) as CUSTOM SPLIT NORMALS on the hair groups only,
  using a Data Transfer modifier from the proxy or `normals_split_custom_set`. Export glTF NORMAL and set the hair
  strip of the normal map to flat. This needs a contract amendment: `conquest_contract_check.py` `flat_shaded`
  ("0 smooth faces") must exempt the hair groups, or redefine the check as "authored normals". That is an
  orchestrator/artist decision. It is still lit shading, not cel.
- **Settling experiment.** One Elias build each way. Measure decoded-vs-proxy (expected about 0 by construction),
  the facet-visibility metric (render luminance step across hair face edges), and portrait/3/4 A/B. Then run a
  Godot 4.6 import probe: "Normals" import on, confirm that LOD generation and vertex compression keep the custom
  normals (Godot 4.2 had a normals-on-import regression, [godot#85406](https://github.com/godotengine/godot/issues/85406)).
  [ASSUMPTION] that Blender 5.0 needs the hair faces flagged smooth for custom normals to apply.

### H6. Scalp cap and hairline: **GAP (partial)**
- **Best practice.** Inner layers cover the scalp and outer layers define the silhouette
  ([yelzkizi hair cards guide](https://yelzkizi.org/blender-hair-tutorial-hair-cards/) *(search summary)*). Hair
  vertex colours feed the final look alongside the silhouette ([Mourino, 80.lv](https://80.lv/articles/005cg-cgma-student-project-hair-for-games)).
  In anime figures and models, the gaps between locks show the hair's own shadow colour, not black and not skin.
  [ASSUMPTION, from the reference set; no primary source found.]
- **What we do.** A dark inner cap ("darker than v1"). Residuals: grey sliver above the mustache (Elias), "narrow
  dark gaps between hairline tips" (Elias), "thin dark crease behind the hairline" (Varden), grey cap visible
  between Wren's locks.
- **Change.** CAP tone = the hair's own shadow tier, never darker. Head-skin vertices within 3-6 mm inside the
  hairline / beard edge take a per-corner gradient from the hair root colour to skin, so a gap reads as thinner
  hair, not a hole.
- **Settling experiment.** Pixel count of dark/grey gap pixels inside a hairline band mask (front + 3/4) before
  and after, on Elias v3 and Varden v1.

### H7. Flyaways / breakup strands: **ALIGNED**
- **Best practice.** Flyaways add "a huge amount of liveliness" ([Mourino](https://80.lv/articles/005cg-cgma-student-project-hair-for-games)).
- **What we do.** HAIR_LOOSE / tousle flicks. Keep them few, per the focal-point rule (H1).

### H8. Long beard (Elias): **GAP**
- **Best practice.** The beard covers the whole jaw surface ("over the entire surface of the jaw"), and three layers
  are enough ([Sosa, 80.lv](https://80.lv/articles/making-hair-and-beards-for-aaa-games)). The stylized rule (H1)
  applies: a conforming volume whose SILHOUETTE edge breaks into a few large clumps, with grooves carried by shading.
  The binding spec said the same thing: "the beard as groomed masses" (review-log, Elias).
- **What we do.** 24-30 narrow ribbon locks with real thickness, rooted along the edge of a painted under-zone, plus
  a dark core. The still reads as shingled planks, and it costs 6,358 tris (more than the scalp).
- **Change.** Add BEARD_SHELL. Offset the beard zone's skin by a thickness field (thickest at the chin), and cut the
  lower edge into 5-9 lobed clumps (Elias) with groove lines as tuck-shade curves. The mustache becomes 2-3 lobed
  shells per side. Keep at most a handful of ribbon locks as tertiary breakers at the silhouette. Normals come from
  the shell's own smooth offset surface (H5).
- **Settling experiment.** Elias v4 A/B: tri delta [ASSUMPTION: about 3-4k freed], hair-diag crossings, and the
  artist's call on "groomed masses".

### H9. Short-cropped beard + stubble (Varden, our weakest spot): **GAP**
- **Best practice.** For stylized work, detail that does not change the silhouette is painted, not modelled: "I don't
  need to give extra geometry to something that isn't altering the silhouette" (Nielsen, Blizzard,
  [80.lv](https://80.lv/articles/004adk-talking-about-stylized-character-art)). Facial hair needs soft, faded roots
  ([Sosa](https://80.lv/articles/making-hair-and-beards-for-aaa-games): gradient-mapped roots). Stubble lives in
  colour on the skin; the transparent shell/fin route is the realistic-tier variant
  ([80.lv search summary](https://80.lv/articles/004adk-talking-about-stylized-character-art)).
- **What we do.** A short crop built from about 20 hanging ribbon locks (5,794 tris) over a dark painted zone. STATE
  residual: "cheek beard locks read as vertical strips". Skin shows through at the chin, and the mustache has a
  149 deg spine kink.
- **Change.** BEARD_CROP mode, reusing the H8 shell. Offset 2-4 mm, following the jaw. The silhouette edge at the
  cheek line and under the jaw is a small-tooth serration (deterministic). A painted fade band of 3-6 mm on the skin
  just outside the edge (per-corner gradient, beard colour to skin) gives the stubble read. Grey streaks are painted
  bands along the flow on the shell. The mustache is 2 clump shells. No hanging strips.
- **Settling experiment.** Varden v2 A/B at portrait, 3/4 and gameplay size. Metrics: tris (target under 2k), max
  silhouette offset from the jaw, and zero skin pixels inside the beard mask. Then the artist verdict on the "strip"
  read.

### H10. Anisotropic / ramp hair shading: **ALIGNED (for now)**
- **Best practice.** Genshin and Honkai use anisotropic or ramp-based hair highlights
  ([Adrian Mendez breakdown](https://adrianmendez.artstation.com/projects/wJZ4Gg),
  [NoiRC blog](https://noirccc.net/blog/posts/54)).
- **What we do.** A painted static angel ring. This matches the FE figures, and the artist rejected the toon
  treatment. No change now. H4 makes Godot anisotropy a one-flag import option later, if wanted.

---

## FACE

### F1. Smooth-read face on flat-shaded geometry (normal bake vs flat facets): **ALIGNED**
- **Best practice.** Anime faces are "hand crafted" in their normals to get a clean read
  ([Motomura](https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf); [Xelabo](https://xelabo.tumblr.com/post/144049889178/mini-tutorial-normal-editing-for-3dcg-anime)).
  Planes-of-the-head shading (Asaro) is the painter's simplification for realistic lighting
  ([reference](https://jonathanpatterson.com/playground/asaro-head-lighting-reference.html)). FE/anime faces instead
  suppress mid-plane shading.
- **What we do.** FACE_NORMAL_REF flat at 1.3-1.8 texel/mm. Face facets are small and sit near the smooth surface,
  so unlike hair (H5) they stay inside the tangent-map hemisphere. No change. Use the same custom-normal route only
  if a face metric shows decode error.

### F2. Authored shadow shapes (jaw, fringe) vs AO: principle **ALIGNED**, edge quality **GAP**
- **Best practice.** Artists set per-vertex "more likely to be shaded" values (vertex colour as a threshold offset),
  and these come out "very clean... without any pixelation" ([Motomura](https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf)).
  Genshin authors face shadows as a dedicated map rather than leaving them to geometry ([NoiRC](https://noirccc.net/blog/posts/54)).
- **What we do.** Drawn shadow-shape regions with AO floors. This is right. But Wren's fringe shadow on the
  forehead has a stair-stepped edge in wren_v7_lockids_hair_close.png.
- **Change.** Shadow-shape boundaries become smooth cut curves at sub-face resolution (we already have the cut
  machinery), or a per-corner gradient instead of per-face region membership.
- **Settling experiment.** Edge jaggedness (angular deviation of the rendered boundary from its fitted spline)
  before and after on Wren. [ASSUMPTION] about the mechanism: the stair-steps could also come from bake resolution.
  Instrument first.

### F3. Topology and where the polys matter: **ALIGNED** (budget note)
- **Best practice.** Topology serves silhouette and deformation loops (eyes, mouth, nasolabial, jaw). Interior detail
  goes into bakes, and loop density is budget-driven
  ([thundercloud](https://thundercloud-studio.com/article/topology-for-low-poly-game-characters/),
  [vsquad](https://vsquad.art/blog/modeling-guide-to-achieving-good-face-topology), both *(search summary)*).
- **What we do.** The MPFB2 base keeps the standard face loops. Budget pressure comes from the hair: facial hair
  costs more than scalp hair on both bearded units. H8/H9/H2 free that budget. Spend it on jaw/cheek/nose
  silhouette loops and mouth/eye loops for the coming expression clips.

### F4. Eye construction: **ALIGNED**, with one age **GAP**
- **Best practice.** Stylized eyes must hold up from the side. The routes are flattened eyeballs, a concave or
  floating iris, or extra inverted-normal sclera/iris shells
  ([Blender Artists eye insights](https://blenderartists.org/t/3d-anime-or-pretty-much-all-stylized-character-eye-design-insights/1118467),
  [MAO via 80.lv](https://80.lv/articles/here-s-how-you-make-3d-characters-eyes-look-prettier-from-the-sides)).
  For age: older characters get eyes "vertically narrower" with "slightly smaller irises" and corners that decline
  ([Clip Studio, Cezziiiii](https://tips.clip-studio.com/en-us/articles/7031)).
- **What we do.** A geometric socket and eyeball blend, an iris built from rings (sharp, resolution independent, as
  in GG Xrd), a round highlight, and a lash band. These match. But Varden's iris is at 61.6 %, LARGER than
  16-year-old Wren's 58 %, while age practice shrinks it.
- **Change.** An age-indexed iris/opening table (youth 58 %, 45+ about 52-55 %, 60+ about 50-53 % plus lid
  height) as a house-style row.
- **Settling experiment.** Varden at 61.6 % vs 54 % A/B. Optionally, a side-view bulge check on the 1.22-1.30
  scaled eyeball (profile render, measure eyeball protrusion past the lid line).

### F5. Mouth / lips: **ALIGNED**
- **Best practice.** In FE/anime the mouth is a single line with minimal lip form; elderly lips get "thinner"
  ([Clip Studio](https://tips.clip-studio.com/en-us/articles/7031)).
- **What we do.** One unified line at Ashe ratios, subtle lip volumes, paler tint. Fold "thinner lips for 60+"
  into F6's age table.

### F6. Ageing a face without realistic wrinkle geometry (the live probe fight): **GAP**
- **Best practice.** Anime/manga ages a face through (a) proportion: smaller, narrower eyes, a sharper jaw, thinner
  brows and lips for the elderly; and (b) a FEW deliberate strokes in a set order. Level 1 is "eyes, crow's feet +
  subtle eye bags, and nasolabial folds"; level 2 adds forehead, chin and neck lines
  ([Clip Studio, SteffyStyle](https://tips.clip-studio.com/en-us/articles/7050)). Another tip says to make the
  nasolabial furrow the thicker line ([Clip Studio, Cezziiiii](https://tips.clip-studio.com/en-us/articles/7031)).
  Our own construction reference (Alicia) treats features as paint on simple smooth geometry.
- **What we do.** The MPFB age macro at 0.80 adds realistic lid and bag GEOMETRY [MEASURED: flatness 1.3-2.4 mm from
  the macro alone]. Painted lines are crow's feet + forehead (+ frown on Varden). There is NO nasolabial line, the
  #1 anime age marker. "Under-eye is flat" is a youth rule (from Wren) applied at every age.
- **Change.**
  - (a) Age decouple: the age macro drives body and skull proportions, but the FACE zone geometry is restored from
    an age-0.5 head (the same pattern as the HAIRSTABLE decoupling). Age then comes only from the age-indexed
    dials (F4 eye table, jaw, brow weight, lip thickness).
  - (b) STROKE line set, in the anime order: nasolabial (new, the boldest), crow's feet, plus a short under-eye stroke
    on the outer third for 45+, then forehead. Each stroke tapers 0 -> w -> 0, with a minimum width derived from the
    gameplay head pixel height (see F7).
  - (c) The face probe measures GEOMETRY flatness with authored stroke regions masked out, and reports a separate
    stroke inventory. The flat-under-eye law then says "no geometry", not "no stroke".
- **Settling experiment.** Elias v3 vs (a+b+c). Targets: geometric flatness back at Wren-class (at most about 1 mm),
  probe pass with strokes present, portrait + gameplay-size panels, artist verdict. [ASSUMPTION] about the mechanism
  of the 5.5 mm reading: the line cuts land inside the probe's columns. Varden's dense-crease spike sits at
  dx +19-19.5 mm, where crow's feet start (gap 3.5 mm past the 16 mm corner). Confirm by re-running the probe with
  FACE_LINES=None and the macro unchanged; STATE already gives 1.3-2.4 mm for that case.

### F7. Line and scar legibility: **GAP**
- **Best practice.** Shapes must read at gameplay distance (Nielsen, [80.lv](https://80.lv/articles/004adk-talking-about-stylized-character-art)).
  GG Xrd built a dedicated technique for inner lines (axis-aligned beams on the texture with UVs lined up along
  them) because painted texture lines turn jaggy ([Motomura](https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf)).
  ASW varies line weight deliberately: thin for delicate areas, bold for strength
  ([ASW Academy](https://www.docswell.com/s/ASW_Academy/5LVY67-GG-Toonline-Eng)).
- **What we do.** Lines 0.7-0.8 mm wide and scars 1.0-1.3 mm, all constant width. Varden's forehead lines and
  cheek scars read as thin scratches in the portrait. [ASSUMPTION] that they vanish at gameplay size.
- **Change.** Every stroke (age line or scar) is tapered geometry-cut paint with a minimum width taken from a
  legibility rule (at least about 1.5 px at the in-game portrait head height; derive the number from the Conquest
  camera). Scars become one bold deliberate shape: a pale band of 2-4 mm with a darker lower edge (we already have
  SCAR_SHADOW), plus a brow-hair gap where a scar crosses the brow.
- **Settling experiment.** Add a gameplay-size head panel to every sheet (render at the in-game head pixel height,
  nearest-sample upscale) and measure stroke contrast survival. That panel doubles as the harness for H1/H8/H9.

---

## Pipeline-level

### P1. Probes as gates: **ALIGNED** (ahead of common practice)
Measured hair diag, face probe and determinism twins go beyond what the sources describe. Add the gameplay-size
panel (F7) and the hair facet-visibility metric (H5) as standing probes.

### P2. Godot carriage of normals: **IMPROVEMENT (check)**
Normal maps baked in Blender (MikkTSpace) must meet matching tangents in Godot: export tangents, or let Godot
generate Mikktspace ("Ensure Tangents",
[Godot import docs](https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/import_configuration.html)).
If H5 lands, the import lane verifies that custom NORMAL survives "Generate LODs" on thin hair tips.
[ASSUMPTION] that LOD simplification can collapse sharp tips. Probe on the first hero import.

---

## Verdict tally
By headline verdict per row: ALIGNED 7 (H3, H7, H10, F1, F3, F5, P1), GAP 10 (H1, H4, H5, H6, H8, H9, F2, F4,
F6, F7), IMPROVEMENT 2 (H2, P2). Mixed rows: H5, F2 and F4 have an aligned principle and a gap in the carrier,
edge quality or age handling. They are counted as GAP.

## What we do that best practice contradicts
1. **Beards as many narrow strips over a painted zone.** Practice is a conforming volume with clumped silhouettes,
   with short crops/stubble mostly in colour. The binding spec also said "groomed masses". (H8, H9)
2. **Proxy normals carried by a tangent-space map on flat facets.** Our own report shows the carrier cannot
   represent 27.5 % of them, and GG Xrd avoids normal maps for exactly this. (H5)
3. **Realistic MPFB ageing geometry on a style whose law is "features are paint on smooth geometry"**, while the #1
   anime age marker (the nasolabial stroke) is missing. (F6)
4. **Varden's iris (61.6 %) larger than the youth's (58 %).** Age practice shrinks it. (F4)
5. **A uniform-width lock population with regular tip spacing**, i.e. no primary/secondary hierarchy. (H1)
6. **"Under-eye flat" applied at every age.** Practice keeps the geometry flat but allows a subtle eye-bag STROKE
   from middle age. (F6)

## Top-5 improvements, ranked by impact vs effort (each a lane-sized deliverable)
1. **Hair custom split normals from the proxy** (H5). Effort S-M; impact: every hair still on every unit (removes the
   checkered facets). Needs the flat_shaded contract exemption decided first (orchestrator + artist). Lane: one
   normals pass + contract-check exemption + Godot import probe. Evidence: decode error about 0, facet metric,
   A/B sheet.
2. **BEARD_SHELL / BEARD_CROP** (H8 + H9). Effort M; impact: Varden's worst residual and Elias's plank beard, and it
   frees budget [ASSUMPTION about 3-4k tris]. Lane: shell + serrated/lobed edge + skin fade band in the two builds;
   Varden v2 first.
3. **Ageing stack rework** (F6 + F4 age table + F7 stroke widths). Effort M; impact: settles the live probe fight
   and gives a house rule for every older unit. Lane: face-zone age decouple + stroke set (nasolabial first) + probe
   mask; Elias first.
4. **Lengthwise lock UVs + procedural greyscale ramp x vertex tint** (H4, also feeds H10). Effort M; impact: removes
   blocky tiers on all hair and enables Godot anisotropy later. Lane: UV layout + ramp generator + assemble wiring;
   Wren (approved) as the A/B unit.
5. **Per-group lock union (Manifold boolean) after layer resolve** (H2). Effort S to trial; impact: crossings 0 by
   construction plus buried-face tri savings. Lane: post-pass + determinism twin; run as an experiment before any
   house-style adoption.

Honourable mention: the mass-first hair hierarchy (H1). It is high impact but a taste call against the Wren v7
"segmented ribbons" reading, so it goes to the artist as an A/B before it becomes a lane. The gameplay-size panel
(F7) is a near-free harness that every experiment above should use.
