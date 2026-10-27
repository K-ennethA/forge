# Vampire Warrior session, 2026-10-27: a factual timeline for an independent review

Unit: a stylized game character (the "Vampire Warrior"; the artist's sheet is img/13). It ships in two games. The
artist's goal (docs/handoff): a top-quality stylized-studio master, riggable and animatable, that resembles the sheet.

## The pipeline, as it stands

1. 2D references: Codex image generation draws a front / side / back turnaround from the artist's sheet.
2. A proxy mesh: Pixal3D (an image-to-3D generator, via ComfyUI) turns the 4 views into one mesh with a 4096 texture.
3. The kit (improve/proxy_finish_light.py, "light finish"):
   - stage 1: the proxy into a voxel SDF grid (about 0.025 u cells, with finer boxes), plus config cuts / adds;
   - a watertight remesh (marching tets), then collapse-decimation to a triangle budget;
   - smart-project UVs;
   - a bake of normal / AO / albedo from the proxy;
   - an automatic rig: bones from config joint positions, proximity Gaussian skin weights;
   - placeholder idle / walk clips;
   - a glb export.
4. Separate parts are added after the remesh: the sword (procedural, traced from the sheet); since today the head
   (head_graft mode "mesh", a separately generated head mesh "head7", decimated directly, never voxelised); and the
   cape (procedural).
5. Checks: a contract checker plus the new checks listed below. Codex reviews renders.

Units: the character is about 8.04 u tall. One heavy Blender build takes about 10-15 min on a 32 GB machine.

## Builds this session (renders in img/, numbers in docs/kit-head-path-STATUS.md)

- **SHIPPED G4** (img/00): body6 proxy; head7 fused into the voxel grid; the cape fused to the body and arms.
- **H2** (img/01): head7 as its own part on body6.
  - Eye opening 99.8 % of the source.
  - Visible chin "shelf": body6's own chin was left under the new head.
  - The artist rejected it: "clearly not acceptable and there was issues on the body now".
  - My process errors on H2:
    - I showed a render with a known defect;
    - I told Codex the body was "out of scope";
    - I did not compare against the shipped build.
  - The body texture density dropped 26 %: the head took 4x the atlas.
- **R1** (img/02): H2 + "replace" (the body volume around / inside the head removed above a neck line).
  - The chin shelf was gone.
  - A seam ring across the visible front of the neck.
- The artist asked: "would a full regen of head and body be better ... bald references then the hair later".
  - New bald body attempt v7 (redrawn refs): Codex judged it worse than body6 in every region below the chin
    (img/09).
  - v6_bald (body6's own refs, only the hair painted out, pixel-identical elsewhere): better than body6 (img/10,
    s7).
- **R2** (img/03): s7 bald body + head part + replace.
  - The choker collar is fused to the neck, so the replace carved it: a dark fragment and a pale mottled neck.
- The artist: "the cape on the body stretches to the sword, I thought we had said we werent going to do that
  anymore".
  - Measured (img/07): the idle clip raises the right arm to the sword grip; the cape (fused to the back of the arm
    in the remesh) is dragged along.
  - Shipped G4: max edge stretch 27.7x, 1869 edges over 2x.
  - Re-weighting alone could not fix it (fused geometry).
  - Artist chose: the cape as its own part.
- Cape-free body: v6_nocape (Codex image edit of the bald refs, the cape area only; Codex had to invent the back /
  legs behind the cape) -> s56 (img/11). Codex: below s7 in 6 regions. The arms are cleanly separate.
- Procedural cape (img/08): an A-line surface from the cape silhouettes + body clearance, folds, a torn hem; flat
  palette colours, no texture. It took 5 iterations to remove artefacts (sleeve bulges, a boxy envelope, waves).
- **R3** (img/04), crashed twice on my own code bugs first:
  - the cape part stayed under 1.1x stretch in the clips (fixed);
  - the head texture own tile worked;
  - FAIL: the join on s56's wider jaw, body texture coverage 51 %;
  - I found a HARNESS BUG: my config overlay never reached the stage-1 subprocess. R2 and R3 stage 1 still carried
    body6's 519 hair-cut boxes, a neck-bridge capsule and the old rig chains. Several R2 / R3 conclusions were drawn
    from a contaminated build.
- **R4** (img/05, img/06): the harness fixed, the joints refitted, the grip re-solved, the head inner wall dropped,
  the head unwrapped in large islands, no head AO.
  - PASS: the join, open edges, UV, the texel gate.
  - Codex vs shipped (metrics/R4_codex_regress.txt): REGRESSIONS FOUND:
    - the neck join step / ledges;
    - the cape a plain flat sheet;
    - pink thighs;
    - scalp faceting;
    - flat neck planes;
    - collar / shoulder / torso / shin detail lost.
  - Better: the face.

## New checks added this session

- part_join: doubled surface at the head join.
- rom_neck: neck-turn self-intersections.
- clip_stretch: edge stretch in the shipped clips.
- Per-tile UV overlap.
- regress_vs_shipped.py: same-camera renders of shipped vs new, per-band texel density, a Codex whole-model review.

## Process facts

- 6 heavy builds in this lane (the cap is about 3): 2 crashed on my code, 2 contaminated by the harness bug.
- Each fix exposed the next problem:
  - the head part -> the chin shelf -> replace -> the neck seam;
  - a new body -> a fused collar;
  - the cape part -> the arm stretch from the old joints;
  - the joint refit -> an extreme elbow fold.
- The body proxy changed 3 times (body6 -> s7 -> s56); every change dropped some quality somewhere.
- The artist's standing rules: no visible defect shown; a Codex review before showing; face first then hair; the
  highest quality first; budgets are guidance; short reports.
