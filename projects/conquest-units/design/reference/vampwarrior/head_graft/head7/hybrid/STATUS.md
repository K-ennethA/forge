# Vampire Warrior: animatable hybrid head, STATUS (2026-10-27 checkpoint)

The goal is a head part that LOOKS like the source / bake-off variant (b) and DEFORMS cleanly (blink, talk).

- **Source:** `../head7_s56_edit.glb` (sha d2886e4a6aa0, 199.8k tris).
- **Topology:** the MPFB hm08 template head + neck (MPFB extension data in `%APPDATA%\Blender Foundation\Blender\5.0\extensions\user_default\mpfb\data`).
- **Frame:** the exported glb is in the SAME frame as head7_s56_edit.glb (glTF +Y up; Blender build frame f = (x, -z_glb, y_glb); the head is about 0.97 glb units tall).
  - So the kit's existing `head_graft` similarity applies unchanged (yaw 90, mid_z 0.0035, scale 1.07, place [-0.345, 7.515]).
  - The parallel kit session (`head_graft.mode "mesh"`) can take `hybrid_head.glb` directly.

Nothing is committed.

## Files here

| file | what | made by |
|---|---|---|
| `hybrid_head.glb` | the asset: the head plus parts (`eye.L`, `eye.R`, `fang.L`, `fang.R`, `teeth_upper`, `teeth_lower`, `tongue`); armature `head_rig`; skin weights; 7 shape keys; textures embedded | `scripts/hyb_rig.py` |
| `hyb_rigged.blend` | the same scene in Blender (rig, keys, weights). Textures are referenced by absolute scratch paths, so relink them to the PNGs here if needed | `scripts/hyb_rig.py` |
| `hyb.blend` | the unrigged assembled parts after the bake | `scripts/hyb_build.py` |
| `hybrid_albedo.png`, `hybrid_normal.png` | head skin bake (2048) from the source, normal tilt capped at about 22°, bake misses filled | `hyb_build.py` + `post_tex.py` |
| `hybrid_eye_albedo.png` | procedural eyeball texture (sclera, iris, pupil, highlight with the edit's paint values; iris on the edit's iris axes) | `hyb_build.py` |
| `hybrid_head_rig.json` | bones (head, jaw, eye.L, eye.R), the weight scheme, shape-key list and usage note, tris per part | `hyb_rig.py` |
| `rig_report.json` | jaw-weight counts, blink angles, per-pose tests | `hyb_rig.py` |
| `hyb_build.json` | part placement, bake-source drops, pushes | `hyb_build.py` |
| `hybrid_sheet.png` | source / (b) / hybrid face close-ups, eye and mouth zooms, all poses (textured and clay) | `scripts/hyb_sheet.py` (from existing renders) |
| `work/` | frozen inputs, so the chain reruns without the bake-off scratch (see below) | see "Inputs" |
| `scripts/` | every script; `chain.sh <work_dir> [eye_expand]` reruns the whole build | |

To reproduce everything: `bash scripts/chain.sh work 0.0065`.
- On first use it rebuilds `work/s2/edit.npz` and `work/s2/edit_tex.png` from the glb.
- Outputs go to `work/out/`. Each step is a small single-head Blender job (under 2 min, under 3 GB).
- Render scripts: `scripts/render.py <spec.json>` (workbench clay + EEVEE textured; item `blend_all` with an optional `shape_key`).

### Inputs (`work/`)

| file | content | how it was made |
|---|---|---|
| `s2/mpfb_base.npz` | hm08 `base.obj` parsed (V, quads, UVs, body mask, eye helpers, joints) | inline parse of `mpfb/data/3dobjs/base.obj` + `basemesh_vertex_groups.json` |
| `s2/edit_eyecover.npy` | source faces inside the liner ring | `eyemaps_src.py` + `eyecover.py` |
| `s2/edit_eyecontour.npz` | source eye-opening contours | `eyemaps_src.py` + `eyecover.py` |
| `s2/edit_eyemaps.npz` | front ray maps of the eyes | `eyemaps_src.py` |
| `s2/edit_landmarks.json` | nose tip, chin, top, back, ears, neck ring | inline extraction from the front probe map (`probe_src.py`) |
| `s3/src_landmarks.json` | the above plus ear tips / lobes | inline extraction |
| `s3/src_mouth.npz` | lip line (`slit`), lip borders (`up`, `lo`), lower-lip top edge (`lolip`) | inline extraction |
| `s3/src_fangs.json` | fang extents | inline extraction from the front probe map |
| `s3/hm_lip_up_faces.npy`, `s3/hm_lip_lo_faces.npy` | hm08 upper / lower lip faces, split from MPFB `uv_layers/lips_horizontal_split.json.gz` by its UV gap | inline |
| `s3/hm_helper-*_faces.npy` | hm08 teeth / tongue helper faces | inline |
| `s3/hyb_fit.npz` | the current fit (positions, quads, UVs, all vertex sets, warp parameters) | `wrap2.py` |
| `s3/m_hyb.json` | the drift metrics below | `measure.py` |

The inline extractions were not saved as scripts. Their outputs are frozen here; regenerate them only if the source head changes.

## Status per piece

| piece | state | notes |
|---|---|---|
| Landmark restore (source silhouette + features) | **done, partial quality** | `wrap2.py` steps below. Ears are rough (kept as fitted, not re-projected). The nose is fine. Lips are slightly wavy |
| Eyelid loops + eyeballs | **done** | Real lid margin loops: hm08 ring 1, 28 upper / 25 lower vertices, pinned to the source opening. Pockets sit behind the balls (0.9 R). The margins ride on the ball (R + 0.0006). Separate UV-sphere eyeballs (2304 tris each) on their own bones, so the eyes can look around |
| Mouth loops + interior | **done, partial quality** | See below |
| Fangs | **done** | Two tapered, slightly curved cones (width about 0.011, root inside the upper lip, tip at the source fang tip z -0.133). Smaller than the source blocks, as the sheet wants |
| Bake from the source | **done, artifacts left** | See below |
| Jaw / eye bones + weights | **done** | `head` → `jaw` (hinge (0, 0.06, -0.03)), `eye.L`, `eye.R`. Head mesh: jaw = lower lip + chin + lower bag with fades over the cheeks, jaw back and throat; upper lip 0; corners 0.5. Upper teeth and fangs on head; lower teeth and tongue on jaw; eyes rigid. No lid bones: the blink is a shape key |
| Shape keys | **done** | See below |
| LOD0 (one subdivision, about 35k head tris) | **not run** | `scripts/hyb_lod0.py` is written but never executed. It subdivides, re-projects open skin, carries the keys as deltas and re-bakes the normal map |
| Final 0 / 35 / 90 renders of the final state, Codex review | **not started** | The sheet only has face close-ups and poses for the hybrid |

**`wrap2.py` fit steps:**
- an affine + thin-plate warp on sparse landmarks;
- dense pins on the lid margins and lip lines, spread harmonically;
- projection onto the source with the displacement smoothed near the lids, lips and nostrils;
- lid skirts (rings 2–7) and lip skirts interpolated, not projected;
- a fold-untangle pass.

**Mouth:**
- The hm08 lip contact line (22 vertices) is ripped open, giving upper and lower lip loops.
- The upper lip loop sits on the source lip line. The lower lip loop sits on the source's lower-lip top edge, so the mouth is slightly open at rest, as on the sheet.
- Mouth interior: the bag plus the opening faces get a dark material. Tongue and teeth come from the hm08 helpers.
- The teeth are pushed back, so they are barely visible at rest.

**Bake:**
- Normal (tangent, smooth reference) + albedo, head cage 0.010.
- The source's mixed winding is fixed per face against the hybrid surface (about 90k faces flipped). Without this, the normal map had inverted blobs.
- Dropped from the bake source: eyeball shells, the eye surface inside the liner, fang texels, the painted mouth plate, and iris-coloured specks on the lids.
- Leftovers: grey / orange patches on the upper-lid crease (worst above the +x eye) and a small grey patch under the lower lip.

**Shape keys:**
- `blink_L`, `blink_R`: the upper lid rotates about the ball centre to 70% of the opening and the lower lid rises 30%, with falloff over 7 rings. The lid stays on the ball.
- `jaw_open` (18°), `viseme_A` (11° + upper-lip lift), `viseme_O` (7° + pucker), `viseme_M` (lips pressed), `brow_raise` (+0.012).
- The lower teeth and tongue carry the same jaw keys. Use EITHER the jaw keys OR the jaw bone, not both.

## Current numbers (glb units; the head is about 0.97 tall)

**Drift**, visible surface (head skin + eyeballs + fangs) vs the source. `s2c` = source → candidate, `c2s` = candidate → source.

| region | hybrid s2c p50 / p99 | hybrid c2s p50 / p99 | (b) s2c p50 / p99 |
|---|---|---|---|
| face | 0.0033 / 0.031 | 0.0010 / 0.012 | 0.0003 / 0.021 |
| eyes | 0.0035 / 0.024 | 0.0016 / 0.011 | 0.0006 / 0.027 |
| mouth | 0.0041 / 0.036 | 0.0002 / 0.013 | 0.0003 / 0.0013 |
| jaw | 0.0023 / 0.013 | 0.0000 / 0.000 | 0.0003 / 0.0011 |

- The hybrid's s2c p50 is about 10× (b)'s. Most of it is chord error at 8.7k head tris plus the smoothed lid and lip skirts. The planned fix is LOD0.

**Eye opening** (front view, source opening = inside the liner):

| eye | visible eyeball vs source opening area | overlap with the source opening |
|---|---|---|
| -x | 108% | 91% |
| +x | 99% | 87% |

- The lid line is opened 0.0065 beyond the liner's inner edge (`HYB_EYE_EXPAND`). Without it, the lid rim and waterline hid about 38%.

**Lip line:** open fraction 0.94 (source 0.92). Fang area 83% of the source, smaller by design.

**Tris:** head 8,716 + eyes 2 × 2,304 + fangs 2 × 178 + teeth 2 × 96 + tongue 448 = **14,320**. The eyeballs are over-dense; 1,000 each would do.

**Pose tests** (face skin edges; self-intersections = skin triangle pairs, neighbours excluded):

| pose | stretch p99 | stretch max | edges > 1.5× | flipped tris | self-intersections | lid verts inside ball |
|---|---|---|---|---|---|---|
| rest | 1.00 | 1.00 | 0 | 0 | 273 (eyes 107, mouth 108, ears 56) | 0 |
| blink_L | 1.26 | 7.6 | 46 | 15 | 379 (eyes 213) | 0 |
| blink_R | 1.25 | 4.3 | 49 | 12 | 350 (eyes 184) | 0 |
| jaw_open | 2.28 | 20.6 | 126 | 37 | 279 | 0 |
| viseme_A | 1.72 | 12.6 | 96 | 31 | 299 | 0 |
| viseme_O | 1.84 | 7.9 | 128 | 28 | 292 | 0 |
| viseme_M | 1.24 | 4.8 | 21 | 3 | 404 (mouth 239) | 0 |
| brow_raise | 1.22 | 1.35 | 0 | 0 | 273 | 0 |

- The rest self-intersections are pre-existing: lid-rim creases, inner-lip / mouth-opening folds, ear folds.
- About 118 faces are still flagged as folded, mostly the lid-rim creases (a natural sharp crease) and some at the mouth / ears.

## Exact next steps
1. **Lid-crease artifacts** (grey / orange patches above the eyes, worst at +x): look at `work/out/hyb_fit` faces in eye rings 6–12. Either extend the interpolated "soft" lid skirt to ring 9 in `wrap2.py` (soft set: `if 2 <= r <= 7`) or drop the source faces on the lash rim from the bake source (`hyb_build.py`, `keep = ...`). Then rerun `chain.sh`.
2. **Run LOD0:** `blender -b --python scripts/hyb_lod0.py -- <work> <work>/s3/hyb_fit.npz <work>/out`. Then measure: `hyb_export_npz.py` on `hyb_rigged_lod0.blend`, keep parts 0–2, run `measure.py -- s2/edit.npz 0 <json> <npz>` and `post.py`. Target: face s2c p50 at or below about 0.001. Keep LOD1 (this file) as the game LOD.
3. **jaw_open quality:** the mouth corners stretch up to 20×.
   - Set the corner weights to 0.35 (`hyb_rig.py`, `wj[corners]`).
   - Widen the jaw fade over the cheeks: `ss(zl + 0.004, zl - 0.014, ...)` → `-0.025`.
   - Add a corrective "mouth corner" shape for jaw_open > 12°.
4. **viseme_M self-intersections** (lips pressing through): reduce the lower-lip lift to 0.7 × gap, or move the upper lip down by half.
5. **Ears:** the fit is rough. Add 3–4 ear-rim landmarks (source pointed-ear rim vs hm08 helix) in `wrap2.py`, and allow projection there (`hyb_lod0.py` keeps ears unprojected).
6. **Eyeballs:** cut them to about 1,000 tris (`hyb_build.py`: nu 32, nvr 16).
7. Render the final 0 / 35 / 90 textured and clay views (`render.py`, item `blend_all` = `out/hyb_rigged.blend`) into the sheet (`hyb_sheet.py`), then run the Codex review (head refs head-only, aligned, mirrored).
8. **Eyes toward the sheet (easy):** the source eyes are narrower and more hooded than the sheet panel. Opening them is one parameter: `chain.sh work 0.009` (`HYB_EYE_EXPAND`). It moves the pinned lid margins outward from the liner, and the lid rings, bake and blink follow automatically. Lowering the upper-lid crease would need extra landmarks.
9. **Kit hand-off:** once the parallel kit session lands `head_graft.mode "mesh"`, test it with `hybrid_head.glb`. Its frame equals head7_s56_edit.glb, so the existing `head_graft` frame and similarity numbers carry over. The neck boundary is the hm08 cut at template y 5.65 (an open 56-edge loop); it needs the kit's neck-seam rule.

**Known non-issues:**
- Open edges: 100 = the neck loop (56) plus the ripped lip line (about 44, both sides). Intentional.
- The `hyb_*.blend1` backups in scratch were not copied.
- At wrap-up a `blender.exe` (pid 24908) that is not from this lane was running; nothing of this lane was in flight.
