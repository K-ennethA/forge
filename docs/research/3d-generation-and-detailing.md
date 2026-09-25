# 3D generation and detailing: what to adopt, and how detail gets onto the game mesh

Research note, 2026-09-24. No repo code was changed, no service was started,
and the GPU was not used. Numbers marked **measured here** come from
read-only numpy scripts run over two files that already existed on disk:
`projects/werewolf/export/werewolf-form-a.glb` (2026-09-20) and the shipped
game drop `../werewolf/assets/characters/werewolf_escaped/werewolf_escaped.glb`
(2026-09-24 17:45). Both files gave identical numbers. Every unverified claim
is labelled **ASSUMPTION** and names the experiment that settles it.

Scope boundary: `docs/research/ear-pipeline-improvement.md` covers
conditioning-image prep (crop, matte, pad, 1024 px Lanczos, freeze by sha256)
and closed-loop verification. This note does not repeat it. Where a step here
needs a prepared image, it means "prepared per that note, section 2.2".

Standing constraints this note works inside, all read from the repo:

- One RTX 5070, 12 GB VRAM (11.94 usable), 31.6 GB system RAM
  (`meshgen/README.md`).
- Nothing hosted and nothing paid (`docs/automation-thesis.md`, correction 2).
- Models must be MIT/Apache-class. Hunyuan's licence was rejected on purpose:
  EU/UK/KR exclusions, MAU cap, mandatory AI-content labelling
  (`automation-thesis.md` line 19).
- `C:\forge-models\comfyui\custom_nodes\` stays empty. Third-party node packs
  are banned because they pull non-commercial dependencies back in
  (`meshgen/README.md`, "The nvdiffrast trap").
- The character chain is `reference -> design -> generate -> clean ->
  verify_mesh -> rig -> skin -> correctives -> animate -> export`, and a red
  gate blocks the next stage (`docs/architecture.md`, staged build pipeline).

---

## 0. What the shipped character measures (why it reads as choppy)

The artist's complaint is about `werewolf_escaped.glb`. Before choosing any
detailing method, this is what that file contains.

| measurement (measured here) | value |
|---|---|
| triangles / exported verts / welded positions | **16,768** / 9,190 / 8,382 |
| height (glTF Y) | 1.879 m |
| median 3D edge length | **24.8 mm** |
| material | one material, `Material_0.006`, with baseColor, metallicRoughness(+occlusion) and **normal** textures, each 2048 x 2048 |
| faces with near-zero UV area (< 1e-9 of the 0-1 square) | **4,499 faces = 26.8% of faces, 25.9% of surface area** |
| texel density spread of the remaining faces (p5 / p50 / p95, relative to median) | 0.001x / 1x / 13.5x |
| mean texel density at 2048 | 7.97 px/cm (0-1 UV area over 3D area) |
| vertex-normal vs face-normal deviation, p50 / p90 / p99 | 19.9 / 45.7 / **84.0 deg** |
| dihedral angle between adjacent faces, p50 / p90 / p99 | 18.8 / 55.7 / 115.2 deg |
| interior edges over 30 deg / over 60 deg | 7,616 / **2,111** of 25,148 |

What these say:

1. **The textures do not belong to this UV layout.** The three images are
   TRELLIS.2's own atlas: hundreds of small charts (the chart pattern of the
   generator's `UnwrapMesh`). The mesh's `TEXCOORD_0` is a different layout,
   about fifteen large angle-based islands (plotted here). The generator's
   material was carried onto the retopo mesh after the retopo was
   re-unwrapped, so every texel lands on the wrong surface. The base colour
   reads as dark noise, and the **normal map perturbs shading at random**.
   **ASSUMPTION** that the random normal map is the largest single cause of
   "choppy". Experiment E0 (section 5) settles it by rendering the same mesh
   with and without `normalTexture`.
2. **A quarter of the surface cannot carry any texture detail.** 26.8% of
   faces have collapsed UVs. Each of those faces samples a single texel of
   every map, so no bake (normal, colour, AO) can put detail there. The
   visible long triangles that cross the atlas in the plot show that some
   islands were not cut at their seams either. The clean stage recorded
   `uv_coverage 54.1` as its UV gate. Coverage does not detect collapsed or
   stretched faces, so the gate passed.
3. **The geometry itself is coarse and folded in places.** At a 24.8 mm median
   edge, a forearm about 80 mm across has roughly 10 edges around its
   circumference (**ASSUMPTION**: the arm uses the median edge length;
   settled by measuring edge length per body tag), so about 30 deg of turn
   per ring edge is expected geometry, not noise. The p99 face/vertex normal
   deviation of 84 deg is not expected: it indicates folded or near-flipped
   faces, consistent with the 3,028 self-intersections the `verify_mesh`
   stage recorded (`build-plan.json`). A normal map baked over folds
   reproduces the folds.
4. **Hard-edge marking by dihedral angle will add facets on this mesh.** The
   in-progress look lane (`look_werewolf_escaped.py`) marks every edge sharper
   than 60 deg as sharp. On this mesh that is **2,111 edges**, most of them
   on organic surfaces where no crease was intended. **ASSUMPTION** that this
   increases choppiness after the look lane lands. Settled by E0's fourth
   render (sharp-marking off vs 60 deg).

Consequence for ordering: **fix the atlas and the folds before any detail
pass.** Detail baked into a broken atlas is invisible on 26% of the body and
misplaced on the rest.

---

## 1. Shortlist of adoptable models

"Core" means the model runs on ComfyUI's own nodes, with no custom node, so
meshgen can drive it through its existing `comfyui_client.py` without breaking
the empty-`custom_nodes` law. VRAM is the publisher's or a guide's number
unless it says measured.

### 1a. Shape generators (image-to-3D)

| model | licence (weights) | VRAM | ComfyUI core | geometry character | conditioning | characters (stylised now) | printable parts | verdict |
|---|---|---|---|---|---|---|---|---|
| **TRELLIS.2** (in use) | MIT [2] | **8.15 GB peak, measured** | yes [3][4] | O-Voxel; sharp features with forge's tuned remesh; not watertight raw (9 boundary / 210 non-manifold edges, measured in meshgen README); thin parts can collapse (ear note source 3) | single image; no text; seed | baseline; single-view guesses the back | usable for organic decor after voxel repair | keep as default |
| **Pixal3D** (in use) | MIT [5][6] | **9.30 GB single / 11.12 GB at 4 views, measured** | yes, incl. multi-view node [4][7] | TRELLIS.2 backbone, pixel-aligned to the input view [8] | 1 view + MoGe FOV, or 2-4 views on a fixed rig (FOV 20 deg) | **best available lever**: multi-view recovered a slab's proportions to within 4% where single-view was ~3x off (meshgen README) | same, plus `hard_surface` macro | **promote for characters**, fed by rig-camera views (section 2) |
| **TripoSG** | MIT [9] | 8 GB min [9] | no (custom packs only) | SDF, rectified flow 1.5B; publisher claims sharp features; watertight by construction of SDF extraction (**ASSUMPTION** for its released mesher) | single image; **TripoSG-scribble**: sketch + text [9] | trained on cartoons and sketches as well as photos [9]; strongest open candidate for stylised input | SDF output suits print repair | **timeboxed A/B** as a meshgen backend with its own venv (E5) |
| **Direct3D-S2** | MIT [10] | 10 GB at 512, ~24 GB at 1024 [10] | no | sparse SDF; watertight | single image | 512 only on this card | watertight output is the draw for prints | **timeboxed A/B** at 512 (E5) |
| **Hi3DGen** | MIT, NVIDIA NC deps removed by the authors [11] | not published (**ASSUMPTION** 12-16 GB) | no | normal-bridged; aimed at fine geometric detail [11] | single image (via its own normal estimator) | detail-oriented; untested | untested | watch; evaluate only if E5 shows TripoSG/D3D-S2 lose on detail |
| **PartCrafter** | MIT (already queued in `architecture.md`) | 8 GB (queued lane note) | no | part-decomposed meshes | single image | per-part outputs map to tags | multi-part assemblies | keep the queued one-day evaluation |
| Hunyuan3D 2.0 / 2mv / 2.1 shape | Tencent Hunyuan Community: excludes EU/UK/KR, >1M MAU needs permission, outputs must be labelled as machine-generated, outputs may not train other models [12] | 10 GB shape (2.1) [13] | **yes** for 2.0 and 2mv (4-view conditioning node) [14][15] | clean, holeless; Hunyuan3D 2.5/3.0/3.1 are API-only, 2.1 is the newest open weight [16] | 1 or 4 views | strongest open quality-and-consistency reputation [17] | fine | **ruled out by standing policy**; it is the only core-native alternative, so the owner may want to revisit |
| Hunyuan3D-Omni | same Hunyuan licence, EU-gated [18] | 10 GB [18] | no | 2.1-based | **skeleton pose, bounding box, point cloud, voxel control** [18] | pose control is exactly what a rig-ready A-pose needs | bbox control fits parts | ruled out by policy; the capability is the reason to revisit |
| UltraShape 1.0 | Hunyuan 2.1 licence [19] | not checked | no | refines coarse meshes to detailed watertight | mesh + image | detail refiner | watertight | ruled out by policy |
| Step1X-3D | Apache 2.0 [20] | 27-29 GB geometry+texture [20] | no | watertight TSDF, sharp-edge sampling [20] | image; LoRA, symmetry, labels | would suit, but does not fit | would suit | does not fit 12 GB; geometry-only VRAM unpublished (**ASSUMPTION** still >12 GB) |
| SAM 3D Objects | SAM Licence (custom, commercial allowed with AUP) [21] | 32 GB official [21] | no | splat or mesh | image + mask | scene objects | no | does not fit |
| Stable Fast 3D / SPAR3D | Stability Community (free under $1M revenue) [22] | not published | no | low-poly, UV-unwrapped, delit output in under a second [22] | 512 px image | quick stylised props | no | watch; not MIT-class |
| TripoSR | MIT [23] | small | no | feed-forward, fast | image | outclassed by TRELLIS.2-class models [24] | no | no |
| InstantMesh | Apache code; multi-view stage is a fine-tuned Zero123++ [25] | not published | no | LRM mesh | image | dated | no | no (**ASSUMPTION** the Zero123++ derivative inherits CC-BY-NC) |
| Zero123++ | code Apache, **weights CC-BY-NC 4.0** [26] | ~0.9B | no | multi-view images only | image | - | - | no (NC in a product pipeline) |
| SV3D | Stability Community [27] | not published | no | 21-frame orbit video at 576 px [27] | image | - | - | no |
| CraftsMan3D | MIT in HKUST-SAIL repo, AGPL-3.0 in the older repo [28] | not checked | no | native 3D diffusion + normal refiner | image | - | - | no (licence ambiguity, superseded) |
| LGM, Unique3D | MIT (per repos, not re-checked) | - | no | Gaussian / normal-fusion, 2024-era | image | superseded | - | no |
| Rodin, Meshy, Tripo (hosted) | closed | - | - | - | - | - | - | excluded: hosted and paid |

Ranking for forge's two product lines:

- **Game characters:** (1) Pixal3D multi-view on rig-camera views; (2)
  TRELLIS.2 single-view with `structure_n`; (3) TripoSG, pending E5. Hunyuan
  2mv/Omni would rank first on capability and remain excluded unless the
  owner changes the licence policy.
- **3D-printable parts:** (1) parametric Build123d, as the automation thesis
  already rules; (2) Pixal3D/TRELLIS.2 with `hard_surface` and `structure_n`
  for the organic residue; (3) Direct3D-S2 at 512 and TripoSG for watertight
  SDF output, pending E5; (4) PartCrafter for multi-part assemblies.

### 1b. Image models that produce the conditioning views

These do not make meshes. They make the consistent turnaround views the 3D
models need, and they run on ComfyUI core.

| model | licence | VRAM | core nodes | what it gives forge |
|---|---|---|---|---|
| **Qwen-Image-Edit-2509 / 2511** | Apache 2.0, 20B [29] | fp8 ~16 GB, so on 12 GB it runs with ComfyUI's weight offload (slower) [30] | yes: `TextEncodeQwenImageEditPlus`, native template [31] | 1-3 input images, and **"native support for ControlNet: depth maps, edge maps, keypoint maps"** [29]: a reference image plus a depth render gives a view that keeps identity and follows the rig camera |
| **FLUX.2 [klein] 4B** | **Apache 2.0** (the only permissive FLUX.2 weight; 9B and dev are NC) [32] | 8.4 GB distilled / 9.2 GB base [33] | yes, native templates [33] | fast generation and multi-reference editing; no trained depth control (**ASSUMPTION**: a depth image passed as a reference is followed loosely) |
| **Z-Image-Turbo** + Fun Union ControlNet | Apache 2.0, 6B [34] | fits 16 GB class; 12 GB **ASSUMPTION** at fp8 | yes, official template for the union ControlNet [35] | canny / HED / depth / pose / MLSD control [35]; no reference-image input in core |
| SDXL + ControlNet depth | CreativeML Open RAIL++-M | ~8 GB | yes (`ControlNetApplyAdvanced`) | the oldest, best-understood depth-to-image path; weakest identity hold |

IP-Adapter is not in ComfyUI core (it ships as a custom pack), so it is off the
table under the node law. Reference conditioning in core comes from Qwen's
multi-image input and FLUX.2 klein's reference latents.

---

## 2. Recommended generation upgrade path (smallest step first)

**Step 0 (in flight, no new model):** `generate_3d` passes `seed` and
`ensemble`. Without it, twelve regenerations are one sample (ear note, 1.6).

**Step 1: characters go through Pixal3D multi-view by default when views
exist (no new model, no new service).** The werewolf already has a front and
a side reference (`design/refs/form-a-front.png`, `form-a-side.png`, both
full-body, about 500 x 1024 px). `generate_3d(side_image=...)` already routes
to multi-view Pixal3D. The measured slab result (proportions within 4% with
2 views, about 3x wrong with 1) is the reason. The risk is framing: the two
refs are not renders on the core rig. The per-view `ImageCropToMask` at pad
1.1 normalises scale per view, but not the camera model (the refs are close to
orthographic; the rig is a 20 deg FOV perspective). **ASSUMPTION** that this
mismatch is tolerable for a standing figure. E4 settles it by scoring
per-view silhouette IoU of the result against each input mask.

**Step 2: make the views on the rig camera instead of hoping they match it
(image model on core nodes; extends meshgen).** This is the control answer to
"match the reference sheet, do not freestyle":

1. Build a **proportion proxy**: a mannequin at the character's locked
   numbers (`requirements.md` section 3: 188 cm, 50 cm shoulders, 64 cm arm,
   47/49 cm thigh/shin), in the target rest pose. For a re-generation, the
   current rigged mesh is the proxy.
2. Render the proxy's **depth** (and mask) through core's own rig: front,
   right, back, left at FOV 20 deg. `meshgen/tools/make_rig_views.py` and
   `backends/multiview.py` already reproduce that rig to float32 precision.
   This is the queued "parametric-proxy multiview conditioning" lane
   (`architecture.md` line 901) applied to characters.
3. For each view, run **Qwen-Image-Edit-2511** with two inputs: the
   character reference (identity, costume, palette) and the proxy depth
   render (pose, proportion, camera). FLUX.2 klein 4B is the fast arm of the
   same experiment.
4. Gate each generated view deterministically before lifting (section 4):
   its matted silhouette against the proxy mask, landmark rows (crown, chin,
   jacket hem, boot top) agreeing across views, palette distance to the
   reference.
5. Lift with **Pixal3D multi-view** with `structure_n: 5`. The views now
   share the rig by construction, which is the condition the ear note
   (section 2.2) said the sheet panels could not meet.

**ASSUMPTION** that Qwen-Image-Edit holds identity across four views well
enough for Pixal3D. Settled by E3 + E4. **ASSUMPTION** that a 20B model with
offload stays within 31.6 GB of system RAM next to ComfyUI's other resident
state. Qwen is never co-resident with Pixal3D (meshgen runs one job at a time
and ComfyUI evicts between graphs); E3 records the peak working set.

Text-to-3D in forge is always text -> image -> 3D. TRELLIS.2 and Pixal3D have
no text conditioning (their `negative` is a zero tensor, meshgen README).
FLUX.2 klein or Z-Image produce the image, and Step 2 turns it into views.

**Step 3: timeboxed A/B of TripoSG and Direct3D-S2 (new meshgen backends,
own venv, no new service).** meshgen's adapter contract allows a non-ComfyUI
backend with its own venv ("Adding a backend"). Neither model has core nodes,
so this is the only compliant route. Judge both with the existing
`ab_tuning` metrics (silhouette IoU, `edge_sharpness`, `creaseL`,
self-intersections, shells) plus watertightness, on the three synthetic
fixtures and on the werewolf front view. Adopt only a model that beats
Pixal3D on at least one product line's gates. Licence audit applies to the
new venv (`pip list` scan for nvdiffrast, kaolin and the rest of the list in
meshgen README).

**Step 4: watch list, no action.**
- ComfyUI PR #15020 (open, July 2026) adds native Hunyuan3D 2.1 PBR paint:
  mesh -> multi-view -> textured GLB, about 5 GB peak at 6 views of 256 px
  [36]. It would be core-native texturing of an arbitrary mesh. The weights
  stay under the Hunyuan licence, so the policy question is unchanged.
- Hunyuan3D-Omni's skeleton-pose control, for the same reason.
- Hunyuan 2.5 / 3.0 / 3.1 weights. As of September 2026 they are API-only
  [16].

---

## 3. Detailing playbook

### 3.1 Game characters this quarter, at 16.7k triangles

Order matters. Each step names its gate.

**D1. Repair the atlas (no GPU).** Re-unwrap the game mesh (seams at tag
boundaries, as `rigforge_auto_uv` already does), then gate on:
degenerate-UV faces = 0; texel density within 0.5x-2x of the median on
>= 95% of surface area; no UV overlap except the intended mirrored half
(`architecture.md` hard-mirror lane: the mirrored half may share UVs).
Texel density target: 5.12 px/cm is the published third-person baseline and
10.24 px/cm the first-person/hero figure [37][38]. 2048 px at the current
body area gives about 8 px/cm, so 2048 is enough once the atlas is sane.
**Strip the generator's material** from the game mesh at this step; it is
the source of section 0, finding 1.

**D2. Remove folds before baking (no GPU).** Resolve the high face/vertex
normal deviations (p99 84 deg) and the self-intersections with the existing
`mesh_diagnose` / repair tools, then apply a light Taubin-style smooth to the
low-poly only where tags say "organic" (not at the tag seams). Gate: p99
face/vertex normal deviation below a threshold set from a clean reference
mesh (**ASSUMPTION** 45 deg; E0 measures a clean reference to set it).

**D3. Transfer-bake the generator's surface onto the game mesh (CPU Cycles,
existing code).** The standard game workflow is high-poly -> low-poly bake:
normal, AO, curvature and position maps from the dense source onto the clean
game mesh [39][40]. forge already has the normal half: `rigforge.bake_normals`
does a selected-to-active Cycles bake, headless, CPU, 1 sample, cage
extrusion 2% of the object size (`addon/forge/tools/rigforge.py` line 1295).
What is missing:

- the **source**: the generator mesh (277k verts, `form-a-front-2.glb`, per
  `build-plan.json`) with its own UVs and textures. **ASSUMPTION** it is still
  in `werewolf-wip-17.blend` as `werewolf-form-a` (`build-plan.json` names it
  `sculpt_source`). Settled by a headless `get_scene_info` on that blend;
- **base colour transfer**: a second selected-to-active bake (Emit or
  Diffuse-colour pass) of the generator's base colour into the game atlas.
  This gives the semi-realistic look the refs show. The palette lane gives
  the stylised look. The artist picks; both land in the same atlas;
- **AO bake** (Cycles AO, or core ComfyUI `BakeAmbientOcclusion`, which
  takes low- and high-poly meshes [41]);
- **gates on the bake**: fraction of atlas texels whose ray missed the
  high-poly (cage too tight) or hit the wrong shell (cage too loose, the
  armpit and crotch cases); normal-map validity (unit length, blue >= 0.5);
  and a two-run digest to confirm the CPU bake is deterministic
  (**ASSUMPTION**, same method as the retopo determinism check);
- **wiring**: `_bake_material` sets an image node active for the bake but does
  not connect it to the BSDF through a Normal Map node. The glTF exporter only
  writes `normalTexture` when it is connected, so the export step must wire
  it. Godot expects OpenGL-style (Y+) normal maps [42], which is Blender's
  bake default (**ASSUMPTION** that no swizzle override is set; the
  validity gate can check the green-channel convention on a known bump).

**D4. Stylised look from bakes, not from a model (no GPU).** This extends the
in-progress palette lane with three deterministic layers that game artists
use for the hand-painted read [39][43]:

- AO multiplied into the palette colour (crevices at collar, armpits, boot
  tops);
- a curvature (convexity) mask brightening edges (jacket seams, knuckles,
  hair clumps). Blender's Pointiness attribute is Cycles-only; a mesh-based
  curvature computed in numpy over the high-poly and baked through the same
  transfer is deterministic;
- a vertical gradient per region (darker toward the ground).

Godot's `toon` diffuse/specular modes are the engine-side option for the
stylised tier [42]. Each layer is a number (strength), so the artist can tune
it without regeneration, and the palette ΔE gate still applies.

**D5. Procedural micro-detail (no GPU, or seconds of image model).** Surface
texture that 16.7k triangles cannot model: denim twill, leather grain,
stitching lines, boot tread. Two routes, both deterministic:

- bake shader Bump/Noise/Wave nodes into the normal map per region (Cycles
  NORMAL bake of the low-poly's own material; regions from the palette
  lane's classification);
- or ship them as a **Godot detail normal**: StandardMaterial3D blends a
  second albedo/normal through a mask, on UV2 or triplanar, tiled at a higher
  frequency than the atlas [42]. One detail layer per material, so this
  implies one material per region or a custom shader.

Tileable detail maps can come from the image models in 1b (a seeded
"seamless leather grain" prompt), converted to height/normal. DeepBump does
colour -> normal/height with an ONNX model but is GPL and installs ONNX
Runtime with Microsoft telemetry terms [44]; forge's own route is to bake the
image as a Bump input in Cycles, which needs nothing new.

**D6. Shading hygiene at export.** Smooth shading everywhere; mark sharp only
where a region boundary or an authored crease says so, never by a global
dihedral threshold on a generated mesh (section 0, finding 4); triangulate
n-gons for MikkTSpace (the look lane already does); tangents exported.

What 16.7k triangles can and cannot carry:

| detail | possible at 16.7k | how | needs the semi-realistic tier |
|---|---|---|---|
| clothing folds, seams, stitching, zips, buttons, hair clumps | yes, as shading | normal map (D3, D5) | real silhouette on folds |
| crevice shadowing, form readability | yes | AO + curvature + gradient (D4) | - |
| fabric / leather / skin micro-texture | yes | detail normal or baked procedural (D5) | pores, wrinkles from sculpt |
| face features (brow, nose, lips) | as shading only | normal + colour | `face_detail_pass` (second retopo), hero texel density |
| silhouette: separated fingers, spiky hair, thick collar lip | limited: at a 24.8 mm median edge a finger (about 18 mm across) gets 2-3 edges around (**ASSUMPTION**: hands use the median edge) | local density in retopo, or hair cards | yes |
| true displacement | **no**: Godot's Height map is ray-marched parallax, "does not add real geometry" [42] | - | still no in engine; displacement stays offline, baked to normal |
| subsurface skin | no (style) | - | Godot SSS, Forward+ renderer only [42] |

### 3.2 Semi-realistic tier, later

- **Budget:** 30-50k triangles LOD0 (the task-config platform cap is 50k),
  LODs by meshoptimizer once the DLL builds.
- **Materials:** head and body split so the face gets hero texel density
  (10.24 px/cm or more [37]); 2048 each or 4096 body.
- **High-poly source quality:** generate the source at the best settings the
  card allows (`shape_resolution` 1024, since 1536 OOMs on 12 GB, measured),
  then add detail offline in Blender: Subdivision + Displace modifiers driven
  by tileable height maps per region, or a Multires "bake from multires"
  normal pass [45]. These are headless-safe. Sculpt brushes need an
  interactive context and stay with the artist (`automation-thesis.md`,
  "human edge loops").
- **Texture:** projection texturing (3.3, T3) with the reference-conditioned
  image model, delit albedo, a roughness map per region, Godot SSS on skin.
- **Face:** `face_detail_pass` on (second retopo), corrective shapes already
  exist for joints.

### 3.3 Texture generation options, ranked

| rank | method | licence / node status | VRAM | where it fits |
|---|---|---|---|---|
| T1 | transfer bake of the generator's own PBR (D3) | nothing new | CPU | now; semi-realistic look from the same generation |
| T2 | palette + AO + curvature + gradient (D4) | nothing new | CPU | now; stylised look |
| T3 | **projection texturing, forge-built**: Blender renders depth/normal/mask from N cameras; ComfyUI core image model (Qwen-Image-Edit-2511 with reference + depth, or Z-Image-Turbo + union ControlNet depth, or SDXL + ControlNet depth) paints each view; Blender back-projects with angle and visibility weights, inpaints the gaps view by view, bakes to the atlas | Apache weights, core nodes only | 8-16 GB class, offload for Qwen | semi-realistic tier; also stylised via prompt/LoRA. This is the TEXTure / Text2Tex / SyncMVD family of methods [46][47][48], and StableGen (GPL-3 Blender add-on) is a working reference implementation of the Blender half [49] |
| T4 | MV-Adapter texture pipeline | Apache [50] | ~14 GB SDXL variant, <10 GB SD2.1 [50] | new venv backend; SDXL variant over budget (**ASSUMPTION** it fits with offload) |
| T5 | Hunyuan3D-2.1 Paint | Hunyuan licence [12]; official 21 GB [13]; core-native only if PR #15020 merges (~5 GB) [36] | - | excluded by policy |
| - | LumiTex (ICLR 2026) | Apache, with NC third-party components [51] | - | watch |
| - | TRELLIS.2 "texture a given mesh" | exists in the official repo (24 GB, nvdiffrast) [2] and in a banned custom pack; **no core node takes an external mesh** into `Trellis2TextureStage` [52] | - | not reachable under the node law |

---

## 4. Integration design sketch

### 4.1 The candidate-ensemble pattern, applied at every nondeterministic step

Generation is a lottery; forge's gates are the judge. The pattern already
exists in meshgen (`structure_n`: silhouette gate first, then consensus
medoid, never fewer than two survivors). It extends to each place a model
samples:

```
for each nondeterministic step:
    draw N candidates at seeds s, s+1, ... s+N-1        (never random)
    hard-gate each candidate with a deterministic check  (relative margin, >=2 survivors)
    rank survivors (consensus medoid, then a fidelity number, then seed)
    freeze the winner: sha256 + seed + model + options into the stage record
    keep every candidate on disk
```

| step | N (start) | hard gate | rank | cost per candidate |
|---|---|---|---|---|
| turnaround view (per camera) | 4 | matted silhouette vs proxy mask (relative margin); landmark rows agree across views within a tolerance | palette ΔE to reference, then consensus | **ASSUMPTION** 5-10 s klein, 60-120 s Qwen with offload (E3 measures) |
| structure (Pixal3D/TRELLIS.2) | 5 | existing silhouette gate | existing medoid | ~6 s per seed, measured |
| shape tail | 1 | - | - | 82-304 s, measured |
| texture (T3 per view) | 2-4 | seam colour discontinuity across UV seams; projected coverage | ΔE to reference, then consensus | **ASSUMPTION** as turnaround |
| bakes (D3-D5) | 1 | miss-texel fraction; normal validity; two-run digest | - | CPU seconds to minutes |

The queued **structure-locked re-roll** (`architecture.md` line 911) is the
same pattern for the TRELLIS.2 texture tail: keep the accepted shape and
re-roll only the texture seed. It needs no new node, since the texture
sampler's seed is its own widget.

The ratchet law applies to every row: a candidate that scores worse than the
incumbent on the same gates is reverted, with both numbers quoted.

### 4.2 Where the detail passes sit in the stage chain

Add one stage to the `character` chain, **`surface`**, after `animate` and
before `export`:

- It changes no vertex, weight or shape key. Its first gate is that the mesh
  digest equals the rigged mesh's. Texture work can then be redone at any
  time without re-running the rig, skin or correctives.
- Contents: D1 (atlas gate) -> D3 (bakes) -> D4/D5 (look) -> D6 (hygiene).
- Gates: `uv_degenerate_faces` (= 0), `texel_density_spread`,
  `bake_miss_texels`, `normal_map_valid`, `palette_delta_e`,
  `material_matches_uv` (the check that would have caught section 0,
  finding 1: every texture on the export was baked into this mesh's own UV
  layer, recorded by name).
- D2 (fold removal) is geometry, so it belongs in `clean`, and `clean`'s gate
  list gains `uv_degenerate_faces` and a face/vertex normal deviation gate.

For the print line the chain is unchanged: detail on printed parts stays
parametric (`forge_lib.textured_band` and friends), because a normal map
does not print.

### 4.3 Recipes vs commands vs services

| piece | form | why |
|---|---|---|
| repair atlas + strip foreign material | **addon command** (`uv_check` read-only; unwrap exists) + gate | a measurement agents must run in the turn |
| transfer bake (normal, colour, AO) with gates | **addon command**, extending `rigforge.bake_normals` | the bake code exists; it lacks colour/AO passes, gates and BSDF wiring |
| stylised look layers (D4) | **recipe first**, command once repeated | numbers come from the artist's picks; the look lane is already a script |
| procedural detail kit (D5) | **recipe** | per-region material choices are art direction |
| rig-camera turnaround (Step 2) | **meshgen endpoint** `/generate_views` on the existing `comfyui_client` (image-model graph, seeds, ensemble) + **recipe** for the proxy | same ComfyUI host, same job API; not a new service |
| projection texturing (T3) | **addon command** for project + blend + bake; views from `/generate_views` | Blender owns cameras, visibility and baking |
| TripoSG / Direct3D-S2 | **meshgen backends** with their own venv | the adapter contract already allows it |
| anything Hunyuan | nothing | policy |

The recipe library's rule is that every number in a recipe is quoted from a
measured run (`docs/recipes/INDEX.md`). So none of these recipes is written
until its experiment in section 5 has produced numbers.

### 4.4 ComfyUI graphs meshgen can drive without a new service

All of these are core nodes today, so they fit the existing
`comfyui_client` + workflow-template pattern and keep the licence audit
clean:

- **TRELLIS.2, Pixal3D, Pixal3D multi-view**: in use.
- **Hunyuan3D 2.0 and 2mv**: core [14][15], weights policy-excluded.
- **Qwen-Image-Edit-2511** (`TextEncodeQwenImageEditPlus`), **FLUX.2 klein**,
  **Z-Image-Turbo + Fun Union ControlNet**, **SDXL + ControlNet**: core
  templates [31][33][35].
- **Mesh post-processing** in core: `BakeNormalMapFromMesh` (low, high,
  resolution, cage distance), `BakeAmbientOcclusion`, `RenderMesh`
  (ray-cast depth/normal view of a mesh), `PaintMesh` (vertex colours from a
  voxel colour field), `BakeTextureFromVoxel`, `ApplyTextureToMesh` [41].
  `RenderMesh` means the proxy depth views in Step 2 could be rendered inside
  the same ComfyUI graph instead of in Blender. **ASSUMPTION** that its camera
  can be set to core's multi-view rig exactly; Blender's rig renders are
  already verified, so use them unless a test pins `RenderMesh` to the rig.

Model weights to add for Step 2 and T3 (all Apache): Qwen-Image-Edit-2511
fp8 + Qwen2.5-VL 7B fp8 text encoder + VAE (**ASSUMPTION** about 30 GB on
disk), or FLUX.2 klein 4B fp8 + Qwen3 4B encoder + VAE (**ASSUMPTION** about
13 GB). They live under `C:\forge-models\models\`, and `/health` names them as
missing until fetched, per the meshgen contract.

### 4.5 Determinism and never-pay, stated as rules

- Every sampled artefact is frozen by sha256 with its seed, model and
  options in the stage record; replays compare digests.
- Structure replay is bit-deterministic (measured); the shape tail wobbles
  slightly (measured). CPU Cycles bakes are **ASSUMPTION** deterministic and
  get a two-run digest test before any gate depends on them.
- New weights: official publisher repos, safetensors, Apache/MIT only; the
  meshgen licence audit re-runs after every ComfyUI or venv change.

---

## 5. Settling experiments

GPU-minutes are wall-clock minutes of the RTX 5070. Measured unit costs:
TRELLIS.2 304 s, Pixal3D 249 s single / 82-106 s multi-view, structure probe
~6 s per seed (meshgen README). Image-model timings are **ASSUMPTION** until
E3 measures them.

| id | question | method | cost |
|---|---|---|---|
| **E0** | What makes the shipped character look choppy? | headless Blender renders of `werewolf_escaped.glb`, front/side/3-4, fixed light: (a) as shipped; (b) `normalTexture` removed; (c) all generator textures removed, smooth; (d) (c) + sharp marking at 60 deg vs off. Also compute the face/vertex deviation and dihedral table on one clean CC0 reference character to set the D2 threshold. Artist ranks the renders | ~2 GPU-min (Eevee), 0 generation |
| **E1** | Does a transfer bake give usable detail at 16.7k? | re-unwrap with the D1 gate, bake normal + base colour + AO from the generator source, record miss-texel fraction per region, two-run digest | 0 GPU (CPU Cycles, **ASSUMPTION** 3-10 min) |
| **E2** | Procedural detail kit | per-region Bump bakes (denim, leather, stitching) into the E1 normal map; render beside E1 | 0 GPU |
| **E3** | Can a core image model make rig-camera views that keep identity? | proxy depth on the core rig x 4 views x 4 seeds, arms: Qwen-Image-Edit-2511 (fp8, offload), FLUX.2 klein 4B, Z-Image + union depth. Score silhouette vs proxy mask, landmark-row agreement, ΔE to the reference; record peak VRAM and system RAM | klein/Z-Image ~16 images x ~10 s ≈ 3 min each; Qwen ~16 x 1-2 min ≈ 16-32 min (**ASSUMPTION**); total ~25-40 |
| **E4** | Does multi-view beat single-view on the character? | same prepared inputs, `structure_n: 5`: TRELLIS.2 single, Pixal3D single, Pixal3D 2-view (existing refs), Pixal3D 4-view (E3 winners). Score per-view silhouette IoU, OBB proportions vs `requirements.md` numbers, symmetry residual | 304 + 249 + ~95 + ~106 s + 4 x ~30 s probes ≈ 15 min |
| **E5** | Do TripoSG or Direct3D-S2 (512) beat Pixal3D on either line? | new venv backends; the three `ab_fixtures` scenes + werewolf front + one ear input; `ab_tuning` metrics + watertightness | 5 inputs x 2 models x ~1-2 min ≈ 10-20 min (**ASSUMPTION** per-run time); plus 0-GPU setup |
| **E6** | Projection texturing pilot (T3) | 6 views x 2 seeds on the E1 mesh with the E3 winner model; seam discontinuity + ΔE + coverage gates; render beside E1/E2 | ~12 images ≈ 2-24 min depending on model |
| **E7** | Structure-locked texture re-roll | same seed shape, 3 texture seeds via TRELLIS.2 tail | 3 x ~2-4 min ≈ 6-12 min |

Suggested order: E0 -> E1 -> E2 (all essentially free, and they answer the
artist this week), then E3 -> E4 (the generation upgrade), then E5-E7.
Total GPU budget for everything: roughly 60-120 minutes.

---

## 6. Sources

External:

1. Sources of the prior note are not repeated here. "Ear note source N" refers to `docs/research/ear-pipeline-improvement.md` section 5, item N.
2. microsoft/TRELLIS.2 README (MIT; 24 GB official; texturing pipeline for a given shape; nvdiffrast deps): https://github.com/microsoft/TRELLIS.2
3. ComfyUI blog, TRELLIS.2 and Pixal3D native (no custom nodes, no NC deps; mesh post-processing nodes): https://blog.comfy.org/p/trellis2-and-pixal3d-are-now-native
4. ComfyUI PR #14718 (TRELLIS.2 / Pixal3D core support): https://github.com/Comfy-Org/ComfyUI/pull/14718
5. TencentARC/Pixal3D (MIT, SIGGRAPH 2026, multi-view inference): https://github.com/TencentARC/Pixal3D
6. ComfyUI Wiki news, TRELLIS.2/Pixal3D native merge 2026-08-22: https://comfyui-wiki.com/en/news/2026-08-22-trellis2-pixal3d-native-comfyui
7. ComfyUI docs, Pixal3D workflow: https://docs.comfy.org/tutorials/3d/pixal3d
8. Pixal3D paper: https://arxiv.org/abs/2605.10922
9. VAST-AI-Research/TripoSG (MIT, 1.5B, 8 GB, scribble variant): https://github.com/VAST-AI-Research/TripoSG
10. DreamTechAI/Direct3D-S2 (MIT; 10 GB at 512, ~24 GB at 1024): https://github.com/DreamTechAI/Direct3D-S2
11. bytedance/Hi3DGen (MIT, NVIDIA deps removed): https://github.com/bytedance/Hi3DGen and paper https://arxiv.org/abs/2503.22236
12. Tencent Hunyuan 3D 2.1 licence (territory, MAU, labelling, no training other models): https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/main/LICENSE
13. Tencent-Hunyuan/Hunyuan3D-2.1 README (10 GB shape, 21 GB paint, 29 GB both): https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1
14. ComfyUI core `nodes_hunyuan3d.py` (Hunyuan3D v2 + 4-view conditioning): https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_hunyuan3d.py
15. ComfyUI blog, Hunyuan3D 2.0 and multi-view native: https://blog.comfy.org/p/hunyuan3d-20-and-muitiview-native
16. Hunyuan3D versions (2.1 newest open; 2.5/3.0/3.1 API-only as of Sept 2026): https://triposr.org/blog/hunyuan3d-versions
17. 3D AI Studio, Pixal3D vs TRELLIS 2 vs Hunyuan 3D (2026-05-14; qualitative only): https://www.3daistudio.com/blog/pixal3d-vs-trellis-2-vs-hunyuan-3d-comparison
18. tencent/Hunyuan3D-Omni model card (Hunyuan licence, EU-gated, 10 GB, pose/bbox/point/voxel control): https://huggingface.co/tencent/Hunyuan3D-Omni/blob/main/README.md
19. PKU-YuanGroup/UltraShape-1.0 (Hunyuan 2.1 licence): https://github.com/PKU-YuanGroup/UltraShape-1.0
20. stepfun-ai/Step1X-3D (Apache 2.0, 27-29 GB): https://github.com/stepfun-ai/Step1X-3D
21. SAM 3D Objects licence and hardware: https://ai.meta.com/blog/sam-3d/ and https://playground.roboflow.com/models/meta/sam-3d-objects
22. stabilityai/stable-fast-3d model card (Community licence, $1M threshold, UV-unwrapped delit low-poly): https://huggingface.co/stabilityai/stable-fast-3d
23. TripoSR `run.py` / repo (MIT): https://github.com/VAST-AI-Research/TripoSR
24. Hunyuan3D vs TRELLIS vs TripoSR (2026): https://triposr.org/blog/hunyuan3d-vs-trellis
25. TencentARC/InstantMesh (Apache code; customised Zero123++ UNet): https://github.com/TencentARC/InstantMesh
26. SUDO-AI-3D/zero123plus (code Apache 2.0, weights CC-BY-NC 4.0): https://github.com/SUDO-AI-3D/zero123plus
27. stabilityai/sv3d model card (Community licence, 576 px, 21 frames): https://huggingface.co/stabilityai/sv3d
28. HKUST-SAIL/CraftsMan3D (MIT) vs hiyyg/CraftsMan (AGPL-3.0): https://github.com/HKUST-SAIL/CraftsMan3D and https://github.com/hiyyg/CraftsMan
29. Qwen/Qwen-Image-Edit-2509 model card (Apache 2.0, 20B, native ControlNet, multi-image): https://huggingface.co/Qwen/Qwen-Image-Edit-2509
30. Qwen-Image-Edit local VRAM guide (fp8 ~16 GB; offload on smaller cards): https://localaimaster.com/blog/qwen-image-edit-local-guide
31. ComfyUI docs, Qwen-Image-Edit-2511 native workflow: https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511
32. black-forest-labs/FLUX.2-klein-4B (Apache 2.0; 9B and dev non-commercial): https://huggingface.co/black-forest-labs/FLUX.2-klein-4B and https://localaimaster.com/blog/flux-2-local-setup-guide
33. ComfyUI docs, FLUX.2 klein 4B (8.4 / 9.2 GB): https://docs.comfy.org/tutorials/flux/flux-2-klein
34. Tongyi-MAI/Z-Image-Turbo (Apache 2.0, 6B): https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
35. Comfy workflow template, Z-Image-Turbo Fun Union ControlNet: https://comfy.org/workflows/image_z_image_turbo_fun_union_controlnet-7553d92529e0/ and https://comfyui-wiki.com/en/news/2025-12-02-z-image-turbo-controlnet-union
36. ComfyUI PR #15020, native Hunyuan3D 2.1 PBR paint (open; ~5 GB at 6 x 256 px): https://github.com/Comfy-Org/ComfyUI/pull/15020
37. Texel density guideline (5.12 px/cm third-person, 10.24 px/cm first-person): https://www.beyondextent.com/deep-dives/deepdive-texeldensity
38. Epic forum, texel density for a 2048 third-person character: https://forums.unrealengine.com/t/textel-density-for-2048-texture-for-3rd-person-game/678150
39. Stylised game art pipeline (bake normal/AO/position/curvature; AO multiply; position gradient): https://www.unboxfuture.com/2026/07/how-to-sculpt-bake-and-texture-stylized.html
40. High-poly to low-poly normal baking in game production: https://nastyrodent.com/high-poly-to-low-poly-baking/
41. ComfyUI core `nodes_mesh_postprocess.py` (BakeNormalMapFromMesh, BakeAmbientOcclusion, RenderMesh, PaintMesh, BakeTextureFromVoxel): https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_mesh_postprocess.py
42. Godot StandardMaterial3D docs (OpenGL-style normals; Height is illusion only; detail albedo/normal with mask; toon modes; SSS Forward+ only): https://docs.godotengine.org/en/stable/tutorials/3d/standard_material_3d.html
43. Polycount, stylised textures in Blender: https://polycount.com/discussion/175127/tutorial-create-stylized-textures-in-blender
44. HugoTini/DeepBump (GPL; ONNX; colour -> normal -> height): https://github.com/HugoTini/DeepBump
45. Blender manual, Cycles render baking (selected to active, cage, extrusion, max ray distance, multires): https://docs.blender.org/manual/en/latest/render/cycles/baking.html
46. TEXTure: https://arxiv.org/abs/2302.01721
47. Text2Tex: https://arxiv.org/abs/2303.11396
48. SyncMVD: https://arxiv.org/abs/2311.12891
49. sakalond/StableGen (GPL-3 Blender add-on; multi-view projection, weighted blending, bake to UV): https://github.com/sakalond/StableGen
50. huanngzh/MV-Adapter (Apache 2.0; SDXL ~14 GB, SD2.1 <10 GB; mesh texturing pipeline): https://github.com/huanngzh/MV-Adapter
51. LumiTexPBR/LumiTex (Apache 2.0 with NC third-party parts): https://github.com/LumiTexPBR/LumiTex
52. ComfyUI core `nodes_trellis2.py` (no node takes an external mesh into the texture stage): https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_trellis2.py

Repo evidence:

- `meshgen/README.md` (measured costs, VRAM, tuning, ensemble, multi-view, licence audit, node law), `meshgen/config.json`, `meshgen/backends/`
- `docs/architecture.md` (character chain and stage gates, finishing-chain order, queued lanes at lines 899-923), `docs/automation-thesis.md` (licence policy, never-pay), `docs/recipes/INDEX.md` (recipe evidence rule)
- `addon/forge/tools/rigforge.py` (`bake_normals`, `_bake_material`)
- `projects/werewolf/design/requirements.md`, `build-plan.json`, `task-config.json`, `design/refs/form-a-front.png`, `form-a-side.png`
- `projects/werewolf/export/werewolf-form-a.glb`, `../werewolf/assets/characters/werewolf_escaped/werewolf_escaped.glb` (section 0 measurements)
- `projects/werewolf/export/game-drop/look_werewolf_escaped.py`, `sample_palette.py` (in-progress colour lane, read only)
- `mcp/forge_mcp/server.py` (`generate_3d` signature: views supported, no seed/ensemble)
