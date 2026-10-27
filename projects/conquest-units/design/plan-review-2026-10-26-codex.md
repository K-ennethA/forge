**Verdict:** Approve the goal and face-first direction. Revise the plan before implementation. Preserving good source geometry is sensible; “direct decimation + smooth normals + keep the auto-rig” is insufficient for a showcase-quality, animatable master. The one-hour experiment is useful triage; the half-day production estimate is unsupported.

Paths below are relative to `projects/conquest-units/`.

**Issues, highest priority first**

1. **The diagnosis misstates existing shading support.**  
   `improve/proxy_finish_light.py:run_light` already bakes **NORMAL, AO and albedo**, connects the normal map, and supports smooth regions. `vampwarrior_finish.json` already enables smooth shading across head7. However, `smooth_texels` **neutralizes the normal-map texels** in those regions after baking against flat faces. This is a workaround, not a smooth-target detail bake. Adding normal maps is not the missing feature; baking against the final shading basis is.

2. **Geometry damage is plausible, but the images do not isolate its cause.**  
   `proxy_finish.py:build_solid/build_fine` close gaps, fill enclosed regions and offset surfaces. `proxy_finish_light.py:head_graft` additionally folds/mirrors the head, applies inflation plus `shell_u`, cuts it, and blends it into the body. These can merge lids, lashes and teeth before decimation. `pre_decimate_fine` then simplifies; `fine_rebalance` deliberately restores the **coarse pipeline’s triangle share**, potentially limiting the benefit of finer sampling. Flat shading cannot explain a physically reduced eye opening by itself.

   Evidence: head7 raw/edit already has narrower eyes than the sheet; facepass2 worsens readability and chin/fang shape. The earlier head grid also shows gross graft boundaries and missing side surfaces. There are multiple failure sources, not one universal remesh defect.

3. **The proposed master still lacks deformation topology and facial animation.**  
   `proxy_finish_build.py:rig_clips_export` uses proximity-to-bone weights for the shell, rigid single-bone weights for added parts, and explicitly **placeholder clips**. Skipping remeshing does not inherently prevent skinning—this weighting code does not require connectivity—but it does not produce eyelid loops, jaw articulation, shoulder deformation or corrective shapes. QuadriFlow/Instant Meshes output is not automatically animation-ready.

   Separate eyeballs, eyelids, mouth interior and fangs need deliberate construction and binding. Direct decimation is a candidate for rigid pieces and LODs, not the default answer for every deforming master surface.

4. **The contract diagnosis is partly wrong.**  
   `conquest_contract_check.py` already permits declared `skin*` and `hair*` smooth regions. It contains **no open-edge check**. It does enforce triangle ranges as failures. Open-edge reporting also lives in `proxy_finish.py:stage_gate` and `proxy_finish_light.py:run_light`; closed-part expectations appear in `templates/new_unit/BUILD_CONTRACT.md`.

   Revise the actual checks and documentation separately. “Visible holes” alone is too weak: boundary ownership, inward faces, self-intersections and defects exposed during animation still matter.

5. **Separate parts need an assembly and weighting contract.**  
   `run_light`’s existing `light.parts` path gives parts palette materials without the shell’s baked textures; `rig_clips_export` rigid-weights them. It is not a ready-made path for textured heads or flexible locks.

   Define neck boundary positions, matching normals/materials and identical boundary weights—or weld a retopologized neck. Concealed overlap is acceptable only after motion testing. Hair roots should follow the head; the current config parents hair chains to the chest. Existing sine-wave animation is not spring simulation with collisions.

6. **Reference conversion is promising, not validated as mandatory.**  
   `head7_B_render/all.png` is a useful construction reference, but generated reinterpretation can change identity before Pixal3D runs. Approve its landmarks against the original sheet. The edited head is improved, not yet a studio-quality master.

   `vampwarrior_STATE.md` still describes head6/v7.1 while the current config uses head7. Historical `shell_u_why` comments also describe head6. Establish exact source/config/output provenance before comparing results.

**Missing**

- Measurable acceptance criteria: sheet landmarks, visible eye opening, silhouette, close-up texture quality, deformation poses and facial expressions.
- A maintained source master plus authored animation mesh; derivation and rebinding rules for LODs.
- Source-mesh inspection for holes, internal shells, intersections and intended cavities. Automatic hole filling must not seal eyes or mouths.
- Texture/material specification: clean albedo, separate AO, roughness, texel density, padding, tangent consistency and engine lighting tests.
- Runtime budgets beyond triangles: texture memory, material/draw counts, skinning cost and secondary-motion cost.
- Reusable topology/rig/hair interfaces. `wren_s5_hair.py:ribbon_plan/build_lock` depends heavily on surrounding build globals; extract an adapter rather than copying the script. Preserve the style guide’s mass hierarchy and custom hair normals.

**Recommended order and concrete changes**

1. **Process:** Freeze head7 source/config hashes and establish an approved landmark target. Retain untouched source geometry as the control.
2. **Experiment:** Compare source → grafted high → final low, first in matched clay renders. Then compare flat/smooth shading, normal map off/on, and AO off/on. Measure eye aperture, landmarks and regional bidirectional surface error; whole-body p99 can hide facial damage.
3. **Experiment:** Compare direct simplification and automatic retopology against an authored deformation-topology option. Include neck turn, blink, jaw opening and shoulder poses before choosing a winner. Do not impose an arbitrary head budget on the highest-quality control.
4. **[Code] `proxy_finish.py:stage_light`; `proxy_finish_light.py:run_light/head_graft`:** Add an opt-in preserved-mesh route, retaining transforms, UV/material ownership and part identities. Keep SDF repair available per component.
5. **[Code] `proxy_finish_light.py:run_light/smooth_texels`:** Bake against final triangulation, UVs and final smooth/custom normals; preserve valid detail normals. Add component-specific bake sources/cages and keep AO separate from master albedo.
6. **[Code] `proxy_finish_build.py:rig_clips_export`:** Support supplied weights and flexible part chains; add facial controls and deformation validation. Implement runtime spring/collision behavior through target-engine adapters.
7. **[Code/docs] Contract checker, gates, build contract and flow/style docs:** Introduce master/export profiles, intentional-boundary declarations and artist-approved budget exceptions.
8. **Validation:** Run the three named regression digests on the unchanged legacy path, then visual/deformation/export checks on the new path. The unmerged cache branch is an iteration aid; validate cache invalidation for normals, UVs and materials separately.
9. **Production:** Approve the bald head **with neck-motion proof**, build mass-first separate hair, validate collisions, then derive target-specific LODs and textures. Preserve shipped Duskmaw.

No repository files were changed, builds run, or images generated. **The requested `review.md` was not saved:** this session’s enforced filesystem permissions prohibit all writes, including the supplied Temp path.
[exited with code 0]
