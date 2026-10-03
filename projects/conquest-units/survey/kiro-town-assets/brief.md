# Lane brief: Conquest town-buildings + general-asset quality audit (READ-ONLY)

Law: C:/Users/kenne/OneDrive/Desktop/git/forge/docs/lane-conventions.md — read first; its hygiene rules bind you (read-only everywhere except your one report file; no commits; nothing windowed; never launch the Godot editor; headless only; never pay).

DELIVERABLE: an audit of Conquest's town buildings and other general world/prop assets — what exists, what quality it is, and a ranked improvement plan — written to C:/Users/kenne/OneDrive/Desktop/git/forge/projects/conquest-units/survey/conquest_town_assets_audit.md (the ONLY file you write).

CONTEXT: the artist wants the general models (town buildings etc.) reviewed for improvement now that unit models are house-quality. The approved art direction for world assets is: chunky low-poly stylized blocks, flat-shaded, hand-placed charm (grass-on-dirt tiles, rocks, tufts) — NOT flat procedural-shader boxes. Unit house style (for reference on finish quality): forge/projects/conquest-units/design/character-style-guide.md.

TASKS (all inside C:/Users/kenne/OneDrive/Desktop/git/Conquest, read-only):
1. INVENTORY: find every town/building/world-prop model (likely under game/, tile_objects/, board/, assets/, menus/overworld — search for .glb/.obj/.tres meshes/ProcMesh usage). Table per asset: file path, how it's built (imported model vs procedural ProcMesh/CSG), tri count where cheap to read (glb JSON parse — do NOT open the editor), materials (textures vs vertex color vs flat shader), where it appears in game (town view, overworld, battle board).
2. ASSESS vs the art direction: which assets are procedural flat boxes vs genuinely modeled; inconsistent scales/styles; missing flat-shaded chunky look; anything that visibly clashes with the new unit quality bar.
3. IMPROVEMENT PLAN: rank the top candidates (impact on what the player actually sees most, vs effort). For each: keep / retexture-repaint / rebuild-in-forge, one line of rationale. Note which could be forge-built as a batch family (shared palette + style like the unit pipeline).
4. Note any delivery constraints a forge build would need (scales, pivot conventions, how town scenes instantiate buildings — cite file:line).

EVIDENCE: file paths + line numbers for every claim; numbers (tri counts, asset counts) not adjectives. Unknowns listed as unknowns.

Handback <= 40 lines: asset counts by category and build method, the top-5 ranked improvement list with one line each, and any blocker a forge rebuild lane would hit.
