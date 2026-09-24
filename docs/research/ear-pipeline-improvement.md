# Ear pipeline: why the rounds struggled, and what round 4 should do

Research note, 2026-09-23. No repo code was changed. Every number marked
"measured here" was produced for this note by building the committed scripts
offline (build123d in `service/.venv`, `forge_lib` stubbed to `min_wall() =
0.8`) and scoring them with the repo's own `benchmark/silhouette.py`,
`benchmark/geometry.py` and `service/checks.py`. No service was started, and
Blender and the GPU were not used. Unverified claims are labelled
**ASSUMPTION**, and each one names the experiment that would settle it.

"Round 4" means the next ear run. The recipe
`docs/recipes/outline-face-curve-fidelity.md` already uses "round 4" for the
last committed pass (`bdcb71d`). In this note, "rounds 3/4" means the state
committed in `bdcb71d`.

---

## 1. Why the rounds struggled: root causes, ranked

### 1.1 A unit bug made the ear 3x too narrow (primary cause)

`projects/bench-ear-sculpt/part.py` takes its outline from
`ear_outline.json`, which is in the **unit-height** frame: v runs 0 to 1, and
u already carries the aspect (u spans -0.156 to 0.164, so the width is 0.326
of the height). The script then scales both axes by different numbers:

```python
outline = [(side * u * width, v * length) for u, v in _EAR_OUTLINE_DENSE]   # width = 13.7, length = 42
```

u should be multiplied by `length`, not by `ear_width`. As committed, the ear
face is 0.3465 x 13.7 = **4.75 mm wide** on a 42 mm length. The intended
width is about 14.6 mm. The `ear_width` description in the file ("13.7 mm ...
matches the reference outline's own aspect (0.3256)") shows the confusion:
13.7/42 is the right aspect, but the outline already contains that aspect,
so the script applies it twice.

Measured here, with the committed script and only the one parameter changed:

| build | OBB thin / mid / long (mm) | silhouette IoU (L / R) | aspect (ref 0.326) | min_wall |
|---|---|---|---|---|
| as committed (`ear_width` 13.7) | 4.79 / 9.34 / 43.10 | **0.616 / 0.616** | 0.214 | pass, 1.178 mm (680 probes) |
| `ear_width` = 42.0 (u scaled by length) | 9.32 / 14.66 / 43.22 | **0.816 / 0.812** | 0.340 | pass, **1.982 mm** (153 probes) |
| `ear_width` = 42.0, `base_thickness` 4.0 | 4.52 / 14.67 / 42.85 | 0.812 / 0.811 | 0.342 | not run |

The as-committed row reproduces the commit's own numbers (IoU 0.616, min_wall
1.178 mm), so the offline build matches what the agent built. Changing one
parameter clears the 0.74 floor and more than the 0.8 mm wall floor. The
earlier claim that the ear was "~40% too narrow" understated the problem: the
face was 67% too narrow. The next section explains why the scorer reported a
smaller gap.

### 1.2 The scorer measured the ear's side profile, and nothing reported it

`silhouette.mesh_outline` projects the part onto the plane of its two largest
surface-principal axes. The thinnest axis is taken as the face normal. In the
as-committed build the face (4.79 mm) is **narrower than the shell is thick**
(8 mm base plus the dome). The PCA therefore took the **width** as the thin
axis, and the scorer traced the length-by-thickness profile: 8 mm at the root,
tapering to 1.4 mm. That wedge has aspect 0.214, which is where the "~0.2
width-per-height" in `bdcb71d` comes from. The agent was told its ear was
narrow, but the number it saw described the ear's depth, and its own code
comments said the aspect was already 0.326. Nothing in the report exposed the
contradiction: `measure_part` returns `plane_normal` inside `mesh_outline`,
but `quality.py` does not surface it, and it does not surface the OBB extents.

This trap applies to the metric in general. Any thick, narrow part will be
scored on its side profile without warning. The round-2 build did not hit it
(surface variances 10.2 / 39.5 / 126.1); the round-3/4 build did (2.55 / 6.68 /
122.99).

### 1.3 No in-turn silhouette scorer

The agent had `check_model` in the turn, so it could see min_wall numbers, and
it fixed min_wall (0.145 mm, then 0.897 mm, then 1.178 mm across passes). It
had no equivalent for the silhouette gate (commit message `bdcb71d`: "has NO
in-turn scorer for the silhouette gate, so it cannot see what it keeps
missing"). The gate it could measure converged. The gate it could not measure
did not. A scorer that also printed the plane normal and OBB extents would
have exposed 1.1 and 1.2 in one call.

### 1.4 The cup can only be a slot, by construction

`_cup_outline_mm` keeps the pocket between v = 0.14 and `taper_start - 0.05`
= 0.35. This guarantees `wall = base_thickness - cup_depth` by construction,
because the pocket never enters the tapered region. The consequence is that
the cup covers at most 21% of the ear's length. Measured here:

| build | ear face width at v = 0.1 / 0.2 / 0.3 | cup (x by y) |
|---|---|---|
| as committed | 3.57 / 4.38 / 4.70 mm | **1.99 x 8.82 mm**, the "slot" |
| u scaled by length | 10.94 / 13.43 / 14.42 mm | 6.09 x 8.82 mm |

The artist's reference for the inner face (the design-sheet ears, section
2.2) shows a recessed panel inside a rim of roughly constant width, running
from the root tuft almost to the tip. Fixing the scale widens the cup but does
not lengthen it. The taper and the cup compete for the same length. The
constructive rule has to be that the shell stays at least `cup_depth + 1.2 mm`
thick wherever the cup exists, and tapers only past the cup's tip end (see
3.2).

### 1.5 The traced outline was reduced to 10 points

The 144-point traced outline was reduced to 10 points
(`simplify_closed(tolerance=0.012)`), the root and tip apexes were blunted
into shelves, and the result was re-splined. `task.json` records that the
reference extruded straight into a 4 mm prism scores **0.9939**. The
corrected-scale build scores 0.81. The remaining 0.18 is shared between this
simplification, the blunting, the taper, the dome fillet and the cup. This
note did not separate the individual contributions.

`forge_lib.silhouette_part` refuses more than 16 points ("More than that is
tracing pixels"), which pushes authors toward decimation. That rule suits
proportions read off a picture by eye. It does not suit an outline that was
extracted as the acceptance target. **ASSUMPTION** that decimation is the
largest of the five losses. To settle it, build the corrected-scale ear from
the full 144-point polygon with everything else unchanged and re-score. This
needs only build123d and costs seconds.

### 1.6 TRELLIS was not run in rounds 1-4, and the earlier generations did not have the credited property

The benchmark rounds used only `part.py`. That matches the file history: 390
changed lines in `bdcb71d`, the same commit that added the prompt rule
"organic forms never go through PartForge". The premise that the eevee-bowl-v2
generations "carried the correct inner-ear indentation" does not hold up
against the repo:

- `projects/eevee-bowl-v2/design/organic-rework-log.md` section 1: both ears
  were regenerated via `generate_3d` from the sheet crop, "oriented, scaled
  to 70 mm, then ... cut the 1.3 mm inset". The indentation the artist
  approved on 2026-09-21 ("the ears have the correct indentation but no volume
  to them") was that **1.3 mm boolean cut**. That same cut produced the
  min_wall 0 mm failures (467/2769 and 464/2750 probes).
- Measured here on all 12 `models/ear_single_ref*.glb`: every file scores
  **IoU 0.463-0.464, aspect 0.143**, OBB ratio 0.046 : 0.143 : 1.0. The
  interior thickness is about 1.2% of the length (about 0.8 mm at 70 mm). The
  front-face relief is about 0.5% of the length (about 0.35 mm at 70 mm). There
  is no native cup, and the shape is a narrow flat blade. The relief number
  comes from an ad-hoc measure (a 40x40 grid over the face plane, rim-band
  median of the front surface minus the 10th percentile of the interior), so
  treat it as approximate. The silhouette and OBB numbers use the benchmark's
  own code.
- The 12 generations are the same shape. `generate_3d` (the MCP tool) passes
  no `seed` and no `ensemble`, so every call ran the default seed 56 on the
  same image. The meshgen README measures the structure stage as
  bit-deterministic at a fixed seed. Regenerating 12 times bought one sample.
- TRELLIS lost width. The design-sheet ear it was conditioned on has image
  aspect 0.375 (measured here), and the mesh came back at 0.143. **ASSUMPTION**
  on the cause, which the evidence does not separate: the input's truncated tip,
  a second ear intruding into the frame, the oblique view, or the 4x-upscaled
  50 x 89 px source (section 2.1). To settle it, run a 3-arm A/B at seed 56
  (the raw crop, a cleaned crop of the same thumbnail, and a cleaned hero-panel
  crop) and score each with `measure_part`. Cost: about 3 x 5 min GPU. Also
  inspect the prepared mask that the ensemble's structure probe already saves
  (`MaskPreview -> SaveImage`) to see what BiRefNet kept.

### 1.7 Anchoring on a number the repo cannot reproduce

The comments in `part.py` and the recipe build on "round 2 ... measured 0.947
IoU". Measured here, the committed round-2 script (`e36dc1e`) at its default
params scores **0.571 / 0.571** (aspect 0.516; `ear_width` 24 mm on outline
proportions spanning 0.88). The 0.947 came from a Blender state that was not
committed as a script. **ASSUMPTION**: it is in
`benchmark/results/2026-09-23/ear-v2-final.blend`. To settle it, run
`blender_measure.py` on that blend. Round 3's decision to return to "round 2's
curve" rested on that number.

---

## 2. Reference adequacy: verdict and prep pipeline

### 2.1 Verdict

**The shape target is adequate. The conditioning images are not.**

- **`eevee_sheet.png` (anime character sheet) works as the outline target.**
  The extraction is deterministic and stable: with background tolerance
  anywhere from 0.12 to 0.6, the notches stay fixed and the aspect stays
  between 0.325 and 0.328 (`silhouette.py` header). As an image-to-3D input it
  is a poor choice. It is flat-coloured line art, the ear is fused to the head,
  and published work reports that image-to-3D models "struggle to synthesize
  plausible 3D assets when the reference image is flat-colored like hand
  drawings" (Art3D). It also carries no information about thickness or the
  depth of the inner face, because the inner ear is only a colour region.
- **`ear_ref.png` / `ear_single_ref.png` are not adequate conditioning.**
  Measured here, by normalised cross-correlation 0.981, `ear_ref.png` is a
  **4.0x upscale of a 210 x 110 px region** of `design-sheet.webp` (the Parts
  List thumbnail at x 1170, y 210). The ear itself is **50 x 89 px** in the
  source. Its tip starts at y 205, so the crop (starting at y 210) **cuts off
  the tip by 5 px** (20 px after upscaling). The other ear partly intrudes into
  `ear_single_ref.png`, the ear is seen at an angle, and the source is a lossy
  webp. Every published input guide asks for a single, whole, centred subject
  at 1024 px or more (sources 1-4).
- **The two references disagree on the ear's design, but not by much on the
  outline.** The design-sheet ear is a printed panel: a raised rim, a
  recessed panel, a tuft notch at the root, and a root tab (Assembly panel).
  The anime ear is a flat outline. Measured here, the design-sheet ear's
  outline scores **IoU 0.826** against the anime reference (aspect 0.375 vs
  0.326). The cup geometry has to come from the design sheet or from the
  artist, because the anime sheet cannot supply it.
- **Missing entirely: a thickness spec.** "Thicker at the base, a small curve
  in the back" (review log, 2026-09-23) is the only thickness input. The
  8 mm to 1.4 mm profile in `part.py` is the agent's own **ASSUMPTION**. One
  artist number for the root thickness and one for the tip thickness would
  replace it.

### 2.2 Recommended conditioning-image prep (for when TRELLIS is used)

The best source in the repo is the **hero panel's image-right ear** in
`design-sheet.webp`. It is clean, shaded, shows the whole ear including the
root, and shows the rim and recessed panel. Measured here, its island runs
from y 131 to 329 and x 430 to 583. That mask includes the bowl rim below the
ear, so the ear alone is about 125 x 160 px. That figure is **approximate**:
the root has to be cut first. It is still about twice the linear resolution
of the Parts List thumbnail.

1. **Crop at native resolution** with at least 15% margin on every side.
   Assert that no foreground-mask pixel touches the crop border. The current
   crop fails this check.
2. **Separate the ear from what it touches.** Matte the image (BiRefNet, which
   meshgen already uses, or `silhouette.background_mask` plus
   `solid_island`), then cut at the ear/rim junction with the same rule
   `extract_ear_outline` uses (the two deepest convexity defects). Keep one
   connected component.
3. **Composite onto a flat neutral background** (white, or TripoSR's 50%
   grey), or pass RGBA. Hunyuan3D 2.1's preprocessing is: remove the
   background, resize, centre, fill with white.
4. **Pad to a square** so the object fills 85-90% of the long side. TripoSR
   defaults to a 0.85 foreground ratio. meshgen's `ImageCropToMask` uses pad
   factor 1.1, which is about 0.91.
5. **Resample to 1024 x 1024 with Lanczos. Do not use a GAN upscaler.**
   Super-resolution removes blur but does not fix geometric distortion (Hi3D),
   and a GAN can invent edges. The upscale serves the model's input size. It
   does not add information.
6. **Freeze the input**: record the sha256, keep the mask, and measure the
   mask's aspect against the source. Then any mesh-vs-image disagreement can be
   traced to the model and not to the prep.
7. **Run `structure_n: 5`.** On `hard_slab`, 2 of 5 seeds contradicted the
   drawing (IoU 0.18 vs 0.80), and the structure probe costs about 6 s per
   seed (meshgen README). Also try `backend: "pixal3d"` single-view. Pixal3D
   back-projects pixel features into 3D ("pixel-aligned"), which targets
   exactly the silhouette adherence TRELLIS lost here. **ASSUMPTION** that it
   holds the ear's width better. The A/B in 1.6 settles it by adding a
   pixal3d arm.

Multi-view (`views`) is not usable with these references. Pixal3D multi-view
needs renders on core's fixed rig (FOV 20 deg, shared framing and lighting),
and the sheet panels differ in scale, lighting and camera.

---

## 3. Recommended workflow for round 4

### 3.1 How the ears attach to the bowl today (from `projects/eevee-bowl-v2/part.py`)

- **Base side:** two vertical round **lugs** are fused to the outside of the
  body at `_EAR_ANGLES_DEG = (43.7, 316.3)`. Each lug is a cylinder from
  `ear_z0` (neck top + 2 mm) up to the rim top `total_h`. Its radius is
  `lug_r >= bore_r + 3.2 mm`, where `bore_r = 0.5 x 6 + 0.2 + 0.3 = 3.5 mm`,
  and its centre sits at `lug_cr = r_out + lug_r - bite`. Each lug carries a
  **keyed socket**, `socket_for(peg_spec(d=6, l=11), 0.2)`, that opens
  **upward** at the rim top: 11.5 mm deep, 0.2 mm clearance per side, a
  chamfered mouth, and an anti-rotation key slot.
- **Ear side:** `forge_lib.silhouette_part(outline, 9 mm, peg={"d": 6, "l":
  11})`. The part is modelled flat on the bed, and the keyed peg sits at the
  outline's bottom centre, in the part's own plane, pointing -Y, embedded
  `min(0.5d + 1, 0.6 x span) = 4 mm`. The ear drops straight down into the
  socket, and the rib stops it from spinning. `silhouette_part_plan` refuses
  a peg when the part is thinner than, or the root narrower than,
  `d + 2 x min_feature` = **8 mm**.
- **Organic variant (rework log):** the generated ears were leaned outward.
  The peg was unioned at 145.7 deg with a fillet skirt (flare 3 mm, height
  3 mm), and the ears were seated at `[34.3, 0, rim_angle]`. The measured seat
  error was 0.886 mm, against a formula prediction of 0.33 mm. That gap is
  why `bosses.py` (a reversible `attach_boss` / `move_boss` / `detach_boss`
  that retains the added solid) and `seating.py` (`seat_part`: flood-fit the
  bore, verify tip offset, depth, angle and body clearance numerically, or
  refuse) exist.
- The design sheet's Assembly panel matches this scheme: "Insert ears into the
  slots on the sides of the base", and the ears drop vertically, each with a
  root tab.
- This follows standard practice: chamfered pins that funnel into the hole,
  pins printed lying flat so the strands run along the pin, and non-round
  (D, square or keyed) pins against rotation (sources 12-14). FDM clearance
  guidance runs from 0.2 to 0.5 mm depending on the fit. The 0.2 mm slide fit
  in `templates/printer.json` is inside that range.

### 3.2 How the task should specify the interface

The v2 task says "no bowl, no pegs", so the ear was designed to float. Round 4
should build the ear **from its root face outward** in its own local frame,
with the interface given as data:

```
interface:
  root_plane:  local z = 0 is the ear's root; the ear body is z >= 0
  peg:         forge_lib.peg_spec(d=6.0, l=11.0, key=True)   # the SAME spec object the base's socket_for uses
  peg_axis:    local -Z from the root centre, key rib at local +X (toward the ear's front face)
  peg_embed:   4.0 mm (silhouette_part's rule: min(0.5 d + 1, 0.6 span))
  root_land:   the root cross-section must contain a (d + 2*min_feature) = 8 x 8 mm square around the peg axis
  lean:        ear-body tilt relative to the peg axis, degrees (0 for the benchmark; 34.3 was bowl-v2's)
  seat_target: a frozen fixture STL of one lug with socket_for(spec, 0.2) cut, placed at +/-43.7 deg
```

Gates to add, all of them already expressible with the existing tools:

- **the peg exists and matches the spec**: `seat_part` against the fixture
  lug. It verifies tip offset, insertion depth, axis angle and body clearance
  numerically, or refuses;
- **root land**: the part's cross-section at z = 0.5 mm contains the 8 mm
  square (a slice test);
- **the silhouette is scored on the ear body only**: clip at z >= 0 or score
  before the peg is attached. The reference outline ends at the head junction,
  so a 6 mm peg stub would otherwise be counted as shape error.

### 3.3 The steps

**Step 0 (task and tool fixes, before any agent run)**

1. State the units in the task. `ear_outline.json` is unit-height, and **both
   u and v scale by the ear length**. Or ship the outline pre-scaled in mm at
   the task's length.
2. Add the interface block and gates from 3.2. Add the artist's two thickness
   numbers (root, tip) once they exist.
3. Expose the silhouette scorer in the turn (section 4). It must print
   `plane_normal`, the OBB extents, both aspects, and a warning when the thin
   and mid variances are within 2x of each other.
4. Add `seed` and `ensemble` pass-through to the MCP `generate_3d` tool. Today
   it sends only views and backend, so the seed ensemble meshgen already ships
   cannot be reached from an agent.

**Lane A: constructive (primary for round 4; deterministic, seconds per build)**

Evidence for making it primary: one scale fix gives IoU 0.81 and min_wall
1.98 mm (measured here). The mesh lane's best existing evidence is IoU 0.464.

1. In `projects/bench-ear-sculpt/part.py`, scale u by `ear_length`, drop
   `ear_width` as a scale factor, and build the face from the **full traced
   polygon** (or its dense sampling), not from 10 decimated points.
2. Build the cup as a **2D inward offset of the outer outline** (a rim of
   roughly constant width, about 12-15% of local width and at least 1.6 mm,
   **ASSUMPTION** read off the design sheet). Run it from the root tuft to
   about 0.85 of the length. Keep the shell at least `cup_depth + 1.2 mm`
   thick over the whole cup span, and start the thickness taper only past the
   cup's tip end. That keeps the "wall under the cup by construction"
   guarantee with a full-length cup.
3. Shape the back as a dome that leaves a flat central land for bed contact.
   **ASSUMPTION** on print orientation (flat on the back, like
   `silhouette_part`). The artist chooses between a rounded back with
   supports and a flat land.
4. Union `forge_lib.peg(spec)` at the root, per 3.2, in the same script.
   `socket_for(spec)` exists for the base.
5. Place the ear with mirrored yaw. The current `Plane(...)` frame already
   does this correctly (mirror 0.0 deg measured in `bdcb71d`).
6. Verify in the turn: silhouette (the new command), `check_model` (all four
   gates), the thickness profile, and `seat_part` against the fixture lug.
   Save with a receipt.

**Lane B: TRELLIS (the experiment; runs alongside Lane A, judged by the same gates)**

1. Prepare the input per 2.2. Run `trellis2` and `pixal3d` single-view, each
   with `structure_n: 5`: 2 jobs, about 10 min GPU.
2. Import once with `import_generated(repair=false)` to see the raw output and
   measure its silhouette. Then import repaired (adaptive voxel remesh).
3. Orient the ear: face normal to the thin axis, root at z = 0, scaled to the
   task length.
4. Pull the outline to the target with `fit_to_silhouette`. Use `strength=0`
   first to read `iou_before`, then fit with `symmetry=false`. The reference
   is a mask image rendered from `ear_outline.json`, because the command takes
   images, not outline JSON. It warps only in the view plane, so the depth the
   model produced survives.
5. Deterministic cleanup in this order: voxel remesh to one shell (0.2 mm =
   nozzle/2, the `merge_for_print` default), thicken to min wall, then
   **cut the cup deterministically** with the same offset rule as Lane A. The
   raw generations measured about 0.35 mm of native relief, so the cup cannot
   be expected from the model. Then `attach_boss(spec=peg + rib + skirt)` at
   the root, and `seat_part`.
6. Score with the same gates. **Selection rule:** Lane B replaces Lane A only
   if it clears every Lane A gate and the artist prefers its surface in a
   side-by-side render. A mesh the model wanted to produce has no standing of
   its own.

This order matches the hybrid practice in the literature and in the repo:
generate the organic base, then apply deterministic, parametric fixes for
fabrication (InstructMesh: generative models "prioritize visual plausibility
over geometric accuracy", and the fix is targeted edits of thickness and
voids). The repo's own rule in `sculpted-part-hybrid-rule.md` is that
functional geometry is never sculpted: the peg, the root land and the
cup-wall guarantee are functional.

`docs/recipes/organic-path-choice.md` ("organic forms never through part.py")
conflicts with Lane A. That recipe's evidence was runs that saved nothing, a
save and timeout failure. It was not evidence that the constructive shape was
inferior, and the constructive shape failure turned out to be a unit bug
(1.1). **The artist decides**: amend the recipe for outline-defined panels, or
keep it and run Lane B only.

---

## 4. In-turn scorer: feasibility

`benchmark/silhouette.py` is stdlib-only by design. Its header says it "runs
the same under Blender's Python, where the artifact is measured", and
`benchmark/blender_measure.py` already imports it inside Blender, feeding it
evaluated world-space millimetre meshes via
`forge.tools.common.evaluated_mesh_mm`. A read-only socket command
(`silhouette_score {object | match, outline_path, up_axis?}`) is therefore a
thin wrapper. It resolves the objects, calls `evaluated_mesh_mm`, loads the
outline once, calls `measure_part`, and returns `iou`, `mirrored`, `aspect`,
`reference_aspect`, `area`, `plane_normal` (already computed in
`mesh_outline`, currently dropped by `measure_part`), the OBB extents, and a
flag when the thin and mid variances are within 2x.

It belongs in `READ_ONLY_COMMANDS`, with a matching MCP tool beside
`check_model`. Cost measured here in the service venv's Python: under 1 s for
the roughly 4k-facet Build123d ear, and 2-3 s including GLB load for a
200k-face TRELLIS mesh. The only new logic is the variance flag. An agent can
already run the same code today through `execute_blender_python` (import
`benchmark.silhouette` after adding the repo root to `sys.path`, the same way
`blender_measure.py` does), so the gap was discoverability, not capability.

Two guards are needed:

- the ear must be scored without its peg (3.2);
- IoU must never be the only in-turn target. The meshgen README records a
  silhouette-only table that would have shipped the worst mesh (`project10`,
  and `steps25_cfg5` at 1.2 crease length against a true 15.24). The artist's
  cup and thickness requirements stay separate gates.

External evidence that verify-in-the-loop beats blind generation:

- **SceneCraft**, Table 1: full system CLIP-sim 69.8 / constraint 88.9, and
  BlenderGPT 24.7 / 5.6. The ablation rows remove components cumulatively.
  Taking out the render-and-critique inner loop, after the learned library has
  been removed, drops the constraint score from 64.5 to 26.1.
- **L3GO**, ShapeNet-13: accuracy 0.877 (GPT-4V judge) and 0.894 (human)
  against unmodified GPT-4 at 0.346 / 0.403. In its ablation, removing the
  spatial critic drops accuracy from 0.600 to 0.515.
- **CADCodeVerify**: VLM-generated verification questions with corrective
  feedback cut point-cloud distance by 7.30% and raised compile success by
  5.0% for GPT-4.
- **BlenderGym**: inference compute helps most when it is split between
  generation and verification, and the verifier itself improves with more
  compute.
- **3D-GPT** is an open-loop dispatch, conceptualise and model pipeline. Its
  abstract reports no measured verification step.

These papers use VLM critics. A deterministic scorer against a frozen target,
which forge already has, is a stronger signal than a VLM critic, and the
meshgen README cites a medoid-over-VLM result pointing the same way. Forge's
own precedent points the same way: min_wall converged once the agent had
numbers in the turn (1.3), and silhouette did not.

---

## 5. Sources

External:

1. ComfyUI, TRELLIS.2 workflow (BiRefNet removal, centred crop): https://docs.comfy.org/tutorials/3d/trellis2
2. microsoft/TRELLIS.2 README (resolutions 512/1024/1536): https://github.com/microsoft/TRELLIS.2
3. fal.ai TRELLIS 2 guide (at least 512, 1024 recommended; single subject; plain background; fine or thin details can collapse): https://fal.ai/learn/devs/trellis-2-image-to-3d-prompt-guide
4. TripoSR `run.py` (foreground_ratio 0.85, rembg, 50% grey fill): https://raw.githubusercontent.com/VAST-AI-Research/TripoSR/main/run.py
5. Hunyuan3D 2.1 paper (remove background, resize, centre, white fill): https://arxiv.org/html/2506.15442v1
6. Hunyuan3D-2mv model card (the multi-view variant): https://huggingface.co/tencent/Hunyuan3D-2mv/blob/main/README.md
7. Hyper3D Rodin v2 API docs (clean centred references; multi-view improves shape): https://wavespeed.ai/docs/docs-api/hyper3d/hyper3d-rodin-v2-image-to-3d
8. Pixal3D, pixel-aligned generation by back-projection: https://arxiv.org/abs/2605.10922
9. Art3D, flat-coloured illustrations degrade image-to-3D: https://arxiv.org/abs/2504.10466
10. Hi3D (super-resolution removes blur, not distortion): https://arxiv.org/pdf/2409.07452
11. InstructMesh, selective fabrication repair of generative meshes: https://arxiv.org/abs/2608.28534
12. Siraya Tech, 3D print joints (chamfered pins, print joints flat, FDM 0.2-0.5 mm): https://siraya.tech/blogs/news/3d-print-joints
13. Sovol, multi-piece keys and pins (square or D pins against rotation): https://www.sovol3d.com/blogs/news/3d-printing-large-models-in-multiple-pieces-keys-glue-and-assembly-tips
14. Protolabs/Hubs interlocking joints (FDM 0.5 mm tolerance, edge radii): https://www.hubs.com/knowledge-base/how-design-interlocking-joints-fastening-3d-printed-parts/
15. Formlabs interlocking joints (0.2 mm for small parts, up to 0.4 mm for large): https://formlabs.com/blog/how-to-3d-print-interlocking-joints/
16. SceneCraft (Table 1): https://arxiv.org/html/2403.01248
17. L3GO (Tables 1, 3): https://arxiv.org/html/2402.09052
18. CADCodeVerify: https://arxiv.org/abs/2410.05340
19. BlenderGym: https://arxiv.org/abs/2504.01786
20. 3D-GPT: https://arxiv.org/abs/2310.12945
21. Cited in `meshgen/README.md` and not re-verified here: arXiv 2604.27106 (best-of-5, about 26% Chamfer), 2608.09706 (medoid beats VLM verifier), 2511.20624 (ShapeGen DINO normal-map picker).

Repo evidence:

- `projects/bench-ear-sculpt/part.py` (the scale line in `build`, `_cup_outline_mm`), and commits `bdcb71d` and `e36dc1e`
- `benchmark/silhouette.py` (`mesh_outline`, `measure_part`), `benchmark/geometry.py` (`principal_frame`), `benchmark/quality.py`, `benchmark/tasks/ear-sculpt/task.json`
- `projects/eevee-bowl-v2/design/organic-rework-log.md`, `review-log.md`, `check-log.md`, `requirements.md`, `part.py` (`_round_lug_plan`, `_build_base`, `_build_ear`), `models/ear_single_ref*.glb`, `refs/design-sheet.webp`
- `service/forge_lib.py` (`peg_spec`, `peg`, `socket_for`, `silhouette_part_plan`), `addon/forge/tools/bosses.py`, `seating.py`, `silhouette.py` (`fit_to_silhouette`), `model.py` (`import_generated`)
- `meshgen/service.py`, `meshgen/README.md`, `mcp/forge_mcp/server.py` (`generate_3d`, `execute_blender_python`)
- `docs/recipes/constructional-thinness.md`, `outline-face-curve-fidelity.md`, `organic-path-choice.md`, `sculpted-part-hybrid-rule.md`, `peg-boss-junctions.md`, `seating.md`
