# Codex session review, 2026-10-27 (Vampire Warrior pipeline + iteration method)

Requested by the artist: "our work seems to be getting worse or only improving one area, while regressing in the rest". Reviewer: Codex (read-only), given the session timeline, the renders of every build, the checks and the docs. The packet is in the session scratch: session_review/.

## 1. DIAGNOSIS

**You are repeatedly replacing the foundation while trying to polish individual parts. The pipeline has no reliable boundary between an approved asset and an experiment.**

- **Architecture:** voxel fusion destroys separations needed for animation. `img/07_cape_stretch_rest_vs_idle.png` shows the cape attached to the raised arm; shipped G4 reaches **27.7× edge stretch**. Weights cannot restore missing separation. `img/12_head_bakeoff.png` demonstrates another destructive transformation: G4 preserves only about **49% of the source eye opening**, versus approximately 99–100% with direct head decimation.
- **Iteration method:** H2 changes the head but redistributes the shared atlas, reducing body texture density. R1 removes the duplicate chin but introduces a neck ring. R2 changes the body and encounters a fused collar. R3 separates the cape while replacing the body with weaker s56 geometry. R4 fixes configuration and allocation, but inherits that weaker body. These are compound rebuilds, not controlled improvements.
- **Tooling:** the overlay never reached the stage-one subprocess. R2/R3 retained **519 obsolete hair cuts**, a neck bridge and old rig configuration (`docs/kit-head-path-STATUS.md`). Conclusions from those builds are contaminated. Cache shortcuts cannot compensate for incorrect build inputs.
- **Decision-making:** the orchestrator knowingly presented H2’s defect, excluded the body from review, skipped the shipped comparison and exceeded the build cap (`SESSION_SUMMARY.md`). More fundamentally, `docs/review-log-recent.md` records rejecting full retopology because it contradicted the “light flow.” That protects the method at the expense of the stated quality goal.

**R4 improves facial readability and cape independence. It does not establish overall progress.** `img/06_R4_vs_shipped_sheet.png` shows the neck ledge, slab cape, pink thighs and reduced armor definition.

## 2. Is the architecture viable?

**As a proxy generator and automated preview system, yes. As the final production architecture for this goal, no.**

The missing stage is deliberate character construction. Higher voxel resolution, larger textures and more triangles will preserve defects more accurately.

A suitable production route is:

1. **Blockout:** establish proportions, silhouette, armor masses and movement clearance against the original sheet. AI references help interpretation; they must not silently become the design authority.
2. **Sculpt/model:** clean the proxy into intentional anatomy and armor. Separate skin, rigid plates, gloves, cape, hair and weapon. Voxel remeshing is useful during sculpt development; its output should not automatically become the deformation mesh.
3. **Retopology:** construct animation topology around shoulders, elbows, hips, knees, fingers, eyelids and mouth. Template wrapping is a starting point requiring correction. Automatic quad flow or decimation does not establish good deformation.
4. **UV/bake:** author coherent islands, controlled density, adequate padding and stable material allocations. Bake approved high-resolution forms onto the production low mesh using inspected cages and isolated part matching. Keep tangent conventions consistent with export. Blender’s [baking documentation](https://docs.blender.org/manual/en/3.6/render/cycles/baking.html) explains cage and ray-distance controls.
5. **Texture/look development:** paint clean values, color boundaries and material response. Do not treat projected generator texture and baked illumination as finished surfacing.
6. **Rig/skin:** place anatomical joints; use automatic weights as initialization, then paint and test them. Add appropriate forearm/upper-arm twist distribution and corrective shapes. Rigid armor needs deliberate attachment.
7. **Secondary motion:** use cape bones for controlled, predictable movement; simulation or a hybrid for richer motion where supported. Both require collision and extreme-pose testing. Hair remains separate.

Use a relaxed **A-pose** with separated arms and fingers for this character. A T-pose is also valid when required by the rig/retargeting system. Arms-down is poor for inspecting armpits and initializing weights.

This authored high/low, topology-conscious approach matches the responsibilities described in Riot’s [character production role](https://www.riotgames.com/en/work-with-us/job/8279690/3d-character-artist-unpublished-r-d-product-shanghai-china). Studio styles differ; production control is the common requirement.

## 3. ITERATION METHOD

**Lock outputs, then rebuild only affected dependencies.**

| Gate | Explicit locked artifacts | Approval |
|---|---|---|
| Design/blockout | Original sheet, approved deviations, proportions, part breakdown, coordinate frame | Artist approves silhouette and design |
| Forms | Clean body/armor master; bald face and neck; cape blockout | Artist approves all views |
| Production mesh | Topology, part interfaces, bind pose; preliminary deformation proof | Technical approval plus artist deformation review |
| UV/bake | UVs, material slots, texture allocation, high/low pairs, cages, triangulation | Clean bake and surface approval |
| Rig/look | Skeleton, weights, correctives, textures, hair/cape motion | Artist approves expressions and motion |
| Release | Engine exports, clips, LODs, material settings, manifests | Final in-engine approval |

Once body forms are approved, stop regenerating the body. Once production topology is approved, preserve vertex identity. Once UVs are approved, stop automatically unwrapping unrelated parts.

Every experiment needs one hypothesis, one independent variable and a defined success criterion. A head bake experiment must preserve body geometry, UVs, materials and weights. Necessary coupled changes should be named and reviewed together.

Maintain three references: **original design**, **shipped release**, and **latest approved candidate**. A candidate advances only after whole-character comparison; approved improvements become the next baseline.

Record immutable source hashes, resolved configuration, tool versions and stage outputs. Every subprocess must report the same configuration digest. Cache keys must include relevant dependencies.

Preview cheaply using final geometry and shaders with lower bake samples/resolution. Topology and deformation tests require final topology. Use full builds after a hypothesis survives previews. Three failed experiments should trigger diagnosis, not another blind rebuild.

## 4. Recommended Vampire Warrior path

**Keep useful source assets; rebuild the production character around them.**

- **Head:** retain edited `head7_s56_edit` as the face sculpt/look source. Continue the artist-approved animatable hybrid route. The current hybrid is unfinished: documented jaw stretch reaches 20×, lip shapes intersect and eyelids need correction. Approve likeness against `img/13_artist_sheet.png` before locking: head7’s narrow eyes and steep brows already differ from the sheet.
- **Body:** use **bald s7 as the primary reconstruction reference**, supported by body6’s stronger design details (`img/09–11`). Neither is a final animation mesh. Separate/rebuild fused cape and collar regions; model missing surfaces locally. Retire s56 as the master foundation.
- **Cape:** keep procedural geometry as a blockout. Its independence works, but `img/05_R4_side.png` shows a cylindrical curtain. Rework shoulder attachment, fold hierarchy, thickness and asymmetric torn panels. Preserve burgundy lining visibility.
- **Sword:** retain the traced design and procedural source. Validate thickness, bevels and grip. Replace the chest-driven hold workaround with deliberate weapon/hand constraints and a tested elbow solution.
- **Discard:** voxelized face output, obsolete cut-box patches, overlapping neck stumps and generated hair ropes.

Order: approve reconstructed body silhouette and bald face → build continuous head/neck skin to the chest, with separate collar → production topology and deformation proof → UV/bake and palette approval → separate hair locks → final rig, cloth motion and engine validation.

Keep pointed ears and flat boots: those are already approved deviations.

## 5. Quick wins versus big changes

Ranked by final-quality impact:

1. **Big change:** authored body/face topology and corrected skinning. **Artist decision:** approve the production-route change and deformation standard.
2. **Big change:** restore intentional armor forms using s7/body6 references. **Artist decision:** approve proportions, plate hierarchy and silhouette.
3. **Big change:** complete face likeness and functional hybrid expressions. **Artist decision:** approve face before hair.
4. **Medium change:** redesign cape drape and build separate hair. **Artist decision:** approve silhouettes and motion.
5. **Quick win:** unify pale skin, remove pink thighs, clean brows/lashes and restore material/value hierarchy. **Artist approval:** palette and finish.
6. **Immediate process fix:** immutable builds, independent texture allocation and whole-character regression review. No new aesthetic decision required.

## 6. What the checks/review process misses

- **R4 fails three contract checks**, including neck ROM and clip stretch (`metrics/R4_contract_rigged.json`). “13 passed” is not acceptance. Idle stretch is **19.83×**; neck rotation introduces **138 intersections**.
- `part_join` detects duplicate surfaces, not a smooth anatomical transition. Its pass cannot certify the visible neck.
- `uv_health` passes with **5,276 islands** and p95 area distortion **10.16**. Those require investigation. Density measures sampling capacity, not restored detail.
- The head has **9,100 boundary edges** after inner-wall removal. Proximity-based “hidden” classification is insufficient; inspect components and visibility throughout motion.
- Eye preservation measures fidelity to head7, not likeness to the sheet.
- Skin normalization proves valid numbers, not valid weights. Neck tests affecting arms deserve investigation.
- Add wireframes, unlit/clay/material-separated views, facial poses, twist/crouch/reach tests, cloth collisions, foot contact and exported-engine playback.
- Earlier reviews criticize missing heels despite approved flat boots. Supply reviewers an authoritative decision ledger.

**AI review should identify defects and evidence. It must not substitute for artist approval or convert a narrow metric pass into a quality claim.**