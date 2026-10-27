# Plan + goals for review (2026-10-26)

Repo: C:\Users\kenne\OneDrive\Desktop\git\forge. Unit work lives under projects/conquest-units/ (paths below are
relative to it unless absolute).

## Goal (artist-confirmed wording)

1. Characters that RESEMBLE our artist's reference sheets, at the quality of a top stylized studio.
2. Every character is built once as a TOP-QUALITY master, good enough for a showcase / close-up. Methods exist to
   LOWER quality when a target needs it (any game, any camera, any platform), and to KEEP the top quality where it is
   wanted (a showcase). The method is not tied to one game.
3. Every version, top and lowered, is RIGGABLE and ANIMATABLE (clean deformation, sensible topology at the joints and
   the face, a skeleton, clips).
4. The highest-quality option first, always.
5. Budgets are GUIDANCE, not strict. Rough targets: heroes ~35-50k tris + weapon, creatures ~10-25k, lowered versions
   via LODs. If meeting a budget visibly drops quality, quality wins: we flag it and the artist decides.
6. The look the artist has set:
   - no cel shading / outline shells (rejected on vampwarrior v3);
   - faces with open, readable eyes;
   - hair as clean, separate locks;
   - Vampire Warrior: flat boots, pointed ears (artist choices).
7. Fix the recurring "clumpy, like poorly sculpted" result at its CAUSE, not with patches.

## Plan

1. PROCESS: face first, then hair.
   - Approve a bald head with the face first (Codex review + artist).
   - Then build the hair as separate lock pieces with sway bones, reusing the existing ribbon-lock hair system
     (improve/{wren,elias,varden,lyra}_s5_hair.py).
   - Reason: 2D sheets draw face features over hair (a 2D convention), and fused face + hair from one generated blob
     kept failing.
2. REFERENCE STYLE: before the image-to-3D generator (Pixal3D), Codex draws OUR sheet's design as a stylized 3D game
   render (no line art; real eyeballs; crisp colour patches), front / side (mirrored) / back, landmark-aligned.
   - Evidence: design/reference/vampwarrior/head_graft/head7_B_render/ (refs) -> head7/head7_s56_pixal3d.glb (the raw
     head is the best face so far).
3. FINISH PIPELINE (the main change under review). Today's "light" finish:
   - a watertight voxel remesh of the whole proxy (+ opt-in finer-grid boxes, `light.fine`);
   - collapse decimation to a triangle tier;
   - a FLAT-shaded bake of the generator's texture;
   - a contract that requires flat shading and 0 open edges.
   These were set for small, far-off creatures. The evidence that they DEGRADE good meshes:
   - renders/vampwarrior/vampwarrior_h7_raw_vs_edit.png: the edited head7 is good BEFORE the pipeline;
   - renders/vampwarrior/vampwarrior_facepass2_face.png: the same head AFTER it, with small slit eyes, a faceted chin
     and block fangs.
   Proposed:
   - Head / face / hair are never voxel-remeshed. They are kept as separate parts, decimated directly from the raw mesh
     (quadric, feature-preserving), smooth-shaded, with a baked normal map from the raw.
   - The body is repaired (fill holes, drop inner shells) and decimated; the voxel remesh is only a fallback for truly
     broken meshes.
   - The top-quality master gets smooth shading + normal maps + cleaned / repainted textures (de-noised, snapped to the
     sheet palette, a painted value hierarchy).
   - Lowered versions: further decimation / LODs (incl. Godot's import LODs), smaller textures, optionally the lighting
     baked into one diffuse for far cameras.
   - The contract is updated: allow smooth shading; check for VISIBLE holes / non-manifold problems that matter in
     game, instead of requiring a watertight mesh.
   - Rig / clips: keep the auto-rig; add spring-bone chains for the hair / cape.
4. PROOF FIRST: a ~1 h bake-off on the edited head7 (head_graft/head7/head7_s56_edit.glb) compares:
   - (a) the current finish;
   - (b) direct quadric decimation of the raw mesh;
   - (c) automatic retopology (QuadriFlow / Instant Meshes);
   - (d) smooth shading + a baked normal map, combined with (b) or (c);
   each at the head's triangle budget, with drift-to-source numbers, side-by-side renders and a Codex review. Then a
   proposed new rule set goes to the artist for approval, then the kit change (~half a day incl. the regression digests
   vulture_creature 5b3aaa03830de189, drakescale_beast 64758c5e225e4134, sand_drill_worm 879c02377d7e7a44).
5. WORKING RULES:
   - a Codex visual review before the artist sees anything;
   - test builds capped (~3) and batched;
   - no rebuild after a review without the artist's OK;
   - no use of other studios' models / character art as inputs (style described in words only).

## Context to read (the code + steps the plan relies on)

**Code**
- improve/proxy_finish.py: stage 1, `build_solid` (voxel remesh: shell nodes, closing, flood fill, signed distance,
  `min_half_thickness` offset), `build_fine`, `stage_light`, `apply_cut` / `apply_add`.
- improve/proxy_finish_light.py: `run_light`. Covers: head_graft (`shell_u`, `cuts`); `polygonise_fine`;
  `decimate`, `pre_decimate_fine`, `fine_rebalance`; the bake (flat bake reference, cage); `light.parts`;
  `light.smooth`; `light.uv`; recolour / despeckle; the glb export.
- improve/proxy_finish_build.py, improve/rigkit.py: rig, weights, clips.
- improve/conquest_contract_check.py: the contract checks (flat_shaded, open edges, budgets).
- improve/wren_s5_hair.py (and elias / varden / lyra): the existing ribbon-lock hair system.
- A kit speed-up branch (not merged): `claude/interesting-bartik-f8c705` (geometry cache, `--retexture`).

**Docs**
- docs/conquest-new-unit-flow.md (the pipeline steps), design/character-style-guide.md, design/size-table.json.

**Evidence and history**
- design/research/league-modeling-2026-10-26.md and design/research/close-camera-marvel-rivals-2026-10-26.md
  (studio practice).
- design/research/hair-face-best-practices.md (an earlier gap analysis of the hair system).
- design/review-log.md, the 2026-10-24..26 entries: artist verbatim, incl. "clumpy, like poorly sculpted",
  "the image has face features over hair which is a style choice for 2d art but doesnt work on 3d", "always highest
  option first", "all of our characters we assume both and lower quality as needed".
- improve/vampwarrior_finish.json + vampwarrior_STATE.md (the current unit), improve/duskmaw_STATE.md (shipped v6.2).
- renders/vampwarrior/: vampwarrior_h7_raw_vs_edit.png, vampwarrior_facepass2_face.png,
  vampwarrior_facepass_head_grid.png, vampwarrior_v71_*.png.

## Questions for the reviewer

1. Is the GOAL right and complete? Anything missing (e.g. rig / animation quality, texture pipeline, performance,
   reuse across units)?
2. Is the PLAN the best path to the goal? Check it against the actual code: is the diagnosis right that the voxel
   remesh + decimation + flat shading / bake cause the degradation? Point to the functions / settings responsible.
3. What is wrong, risky or missing? (E.g.:
   - can parts that skip the remesh still be rigged and weighted cleanly with rigkit;
   - does the bake / UV path support normal maps + smooth shading;
   - what does the contract check enforce that would block this;
   - how do separate head / hair parts join the body without seams;
   - Pixal3D raw-mesh problems: holes, inner shells, self-intersections.)
4. Is the ORDER right (bake-off -> rules -> kit change -> Vampire Warrior rebuild -> hair pass)? What would you do
   differently?
5. Concrete recommendations, most important first. Mark which are code changes (name the file / function).

Answer as a short written review: verdict, issues (most important first, with file:function references), then
recommendations.

## Review outcome (2026-10-26)

Two independent read-only reviews, both checked against the code:
- Codex: design/plan-review-2026-10-26-codex.md.
- Claude: summarised below.

**Agreed by both**
- The core move is right: the head / face must not go through the voxel SDF.
- The diagnosis is only partly right; there are several causes:
  - `head_graft` SDF inflate + `shell_u` + `close_voxels` + smooth union k 0.04 fill lid / lip / fang gaps (the facepass2
    untextured renders show no eye openings);
  - one global `BAKE_CAGE` (0.20 u) smears eyeballs / teeth / collar into the face;
  - `light.smooth` axis boxes + `smooth_texels` NEUTRALISE the normal map, so the jaw outside the boxes stays faceted;
  - the head gets only ~4-6k tris (~40x cut from 200k);
  - the cube steps come from box cuts;
  - the block fangs are generator geometry.
- My plan was wrong on four points:
  - we already bake normal + AO + albedo;
  - "flat bake" is not the cause (flat low + flat-reference normal map is the style guide's smooth read);
  - the contract has NO open-edge check; it blocks smooth bodies via SMOOTH_OK_PREFIX and only checks the largest mesh;
  - Blender COLLAPSE is already quadric, so "direct decimation" differs only in its source.
- Under-scoped: the rig (proximity Gaussian weights, rigid single-bone parts, placeholder clips, no face bones), the
  part assembly (neck weights, textured parts), the hair system (exec'd sections bound to MPFB globals; needs a module
  extraction, 1-2 days), the export (no tangents), and the acceptance tests (per-region drift, eye-opening area,
  range-of-motion poses). The real kit work is 2-4 days, not half a day.
- Keep the body on the remesh for now (196 sealed cavities + soup in body6); body no-remesh is a later, separate test.
- The goal needs measurable acceptance criteria plus a facial rig scope, a material model and LOD definitions.

**Revised order (merged)**
0. Commit + push the uncommitted kit (origin + backup); re-record the regression digests on that commit.
1. Attribution check (~1 h, config only): rerun the head with inflate / closing / `shell_u` / k minimised and a small
   cage.
2. Head + neck bake-off at ~10-12k head tris:
   - (a) current;
   - (b) decimate the raw;
   - (b+) attribute-aware QEM keeping the raw UVs;
   - (c) QuadriFlow;
   - (e) wrap the MPFB template head onto head7 + bake from the raw.
   Scored on per-region drift, eye-opening area, neck-turn + blink tests and a Codex review.
3. Rules to the artist.
4. Kit change, head path only:
   - head / eyes / neck parts;
   - per-part cage;
   - smooth-reference bake;
   - export tangents;
   - contract fixes;
   - rig part-weight modes (rigid / chain / blend);
   - a range-of-motion report.
5. Vampire Warrior face rebuild -> artist approval.
6. Hair module extraction (hairkit.py) -> hair pass.
7. Body no-remesh bake-off.
8. LOD / export profiles.

## Bake-off result + approved rules (2026-10-27)

Bake-off (scratch bakeoff/s2/bakeoff_sheet.png):
- No config saves the head on the head_graft SDF path. Inflate / shell_u are load-bearing for its unreliable sign test
  and close gaps < ~0.02 u; the eye opening is ~0.03, the lip slit ~0.003. G4 keeps 49 % of the eye opening.
- (b) direct quadric decimation of the source (11k, own UVs, normal + albedo bake, cage 0.004) keeps 99-100 % of the
  eye opening, the lip line and the fangs, with drift p50 0.0003. Best look, but no lid / mouth loops (blink folds).
- (e) MPFB hm08 wrap: the right structure (eyeballs, mouth bag), but its blink collapses and its likeness drifts.
- QuadriFlow and attribute-aware QEM were blocked (non-manifold input / not installed).
- Codex: ship (b) as the provisional head; the master = a (b)-look + corrected (e)-topology hybrid. Even SOURCE has
  narrower, more hooded eyes than the sheet (likeness to the sheet is a separate milestone).

APPROVED RULES (artist, 2026-10-27):
1. Heads / faces never go through the voxel remesh. A head is its own part: decimated directly (look-only) or
   template-topology (animatable). It gets a per-part bake with a small cage and smooth shading + a normal map.
2. The body stays on the remesh for now; a body no-remesh test comes later.
3. Masters may be smooth-shaded. Each unit declares its shading mode; the contract checks every mesh and visible
   holes.
4. Budgets are guidance; quality wins; the artist decides exceptions.
5. Checks: per-region drift to the source, eye opening >= 95 % of the source, a range-of-motion report, and a Codex
   review before the artist.
6. Test builds are capped / batched, using the fast flags.

HEAD ROUTE (artist): the ANIMATABLE HYBRID first. (b) is the stand-in meanwhile.
