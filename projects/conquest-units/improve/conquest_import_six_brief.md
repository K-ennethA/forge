LANE: CONQUEST IMPORT — bombee, aquaoct, canine_bat, raven_rogue, pangolin_knight, shadow_mole (compendium
placeholders, true-size path).

PRECEDENT (follow it exactly, it is proven): the previous import lane, Conquest commit 6ba132b ("units: Magmoo at
8 ft + forge imports seedant / shadowbun / shadow_assassin") — read `git -C <Conquest> show 6ba132b --stat` and its
message, plus forge projects/conquest-units/improve/conquest_truesize_import_brief.md (that lane's brief: the same
law, allowlist pattern, evidence and rules apply here). Sizing law: Conquest e8974f2 (true size via roster
model_scale; root_scale = 1.8/h delivery value; no cell-fit; wide units overhang one tile).
Artist (verbatim, 2026-10-09): "commit and import the rest".
Conquest = C:\Users\kenne\OneDrive\Desktop\git\Conquest (branch feat/forge-unit-import); forge (READ-ONLY to you)
= C:\Users\kenne\OneDrive\Desktop\git\forge at HEAD cc72d3f (copy each projects/conquest-units/rigged/<unit>.glb
byte-identical; verify with git hash-object against the forge HEAD blob).

DELIVERABLE: the six units in Conquest as compendium PLACEHOLDERS: glb + .import (unit_glow_import.gd) + texture
.imports (mipmaps on) under game/characters/models/<biome>/<unit>_forge*, a roster .tres each (donor stats from a
same-role unit, EMPTY moves/abilities/AI, PLACEHOLDER in description + comment, model_scale for the target height),
the six ids appended to CharacterSelect.EXCLUDED_IDS, referenced by NOTHING else.
Sizes: forge projects/conquest-units/design/size-table.json target_height_m for each (all ~1.80 m: the artist has
not sized these yet — flag that in the report; do not invent sizes). Measure each in-engine after import (the
same probe as the precedent: idle t0, CPU-skinned top x model_scale) within 1 % / 0.02 m of its target.
Biomes / donors: pick per unit from existing folders and same-role units, say which and why (e.g. bombee / aquaoct
creature-small; aquaoct = water; canine_bat / shadow_mole = dark; raven_rogue = dark; pangolin_knight = forest or
earth if one exists). Glow: none of the six has emission (all-zero _GLOW); say so from the import log.
Facing: -Y check on all six (forge checker read-only + the Godot-side landmark probe, as the precedent did).

FILES (Conquest allowlist): game/characters/models/<biome>/<unit>_forge* for the six, game/characters/roster/<unit>.tres
for the six, the EXCLUDED_IDS line in menus/CharacterSelect.gd, and tests/unit/test_true_size_units.gd ONLY to add
rows for the six (+ the stale "Magmoo pending" comment from 6ba132b, if still there). Everything else in Conquest is
READ-ONLY (the user's own active work; tests/results/junit.xml untracked — leave it). Never sweep, revert, reformat.
Tools: Godot headless only (C:\Users\kenne\Downloads\Godot_v4.6-stable_win64.exe\Godot_v4.6-stable_win64_console.exe
--headless); GUT with -gconfig= so the user's junit.xml is not rewritten; Blender headless only.

EVIDENCE: per-unit table (biome, donor, root_scale, model_scale, measured idle height vs target, PASS); import-log
glow lines; facing results; suites: test_true_size_units (with the new rows), compendium, select, all tests/unit
(N/M); the grep that the six ids appear only in roster + models + EXCLUDED_IDS (+ the test rows); git status --short.
STOP: done / blocked (report the gap) / grown (report). Never commit, never spawn agents, never launch anything
windowed, never touch port 9876.
