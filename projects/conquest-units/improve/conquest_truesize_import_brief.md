LANE: CONQUEST TRUE-SIZE CHECK + IMPORT seedant / shadowbun (compendium placeholders).

LAW (forge): docs/lane-conventions.md; projects/conquest-units/HANDOFF.md ("Delivery pattern", "Glow", the
CONQUEST working-tree rule). LAW (Conquest = C:\Users\kenne\OneDrive\Desktop\git\Conquest, branch
feat/forge-unit-import): commit e8974f2 "units at true size" (read it: git show e8974f2) plus the docs it
updated: CONQUEST.md "Size", tools/blender/README.md, .claude/skills/blender-unit-import/SKILL.md,
tools/blender/prepare_unit.py, tools/blender/assets.conf, tests/unit/test_true_size_units.gd. Units are now
shown at their DESIGN height via roster model_scale on a fixed 2 m grid, with no cell-fit shrink, and a wide
unit overhangs and stays one tile. Magmoo is held at its old size, pending the artist. That is the current
sizing law: follow it, never re-litigate it.
Artist (verbatim, 2026-10-09): "conquest now shouldnt resize check the model sizes compared to what we have --
have an agent do it" + "impot the new models into conquest".
"What we have" = forge projects/conquest-units/design/size-table.json target_height_m (artist-set in feet
2026-10-09: magmoo 8 ft, petalfang 4, barkling 8, blightcap 2, vampwarrior 6'2", firesprite 4'6", varden 6'4",
lyra 5'8", shadow_assassin 6', mortis 6'2", seedant 3'6", vineweave 5'6"; every other row = the unit's
measured height at the time).

DELIVERABLE 1 (check, read-only to the game): one table, every forge unit in the roster. Columns: unit, roster
.tres, model_scale, .import root_scale, the in-game standing height MEASURED headless in Godot (instance the
unit the way the game does and use the same height measure as Unit.get_visual_height /
test_true_size_units.gd; say which pose), the size-table target, delta (m and ft/in), PASS within 1 % or
0.02 m. Magmoo is expected to differ (held); mark it HELD, not FAIL. Every other mismatch is REPORTED with its
cause (wrong scale, stale glb, a different height measure, ...), and NOT fixed: fixing the sizing system is not
this lane.
DELIVERABLE 2 (import): seedant and shadowbun into Conquest as compendium PLACEHOLDERS, following the
import-wave precedent (git show 287ff9f / 9a90d02 / 01b5b2e). That means:
- the forge rigged/<unit>.glb (forge HEAD 5ecd3af) copied to game/characters/models/<biome>/<unit>_forge.glb,
  plus its .import (import_script unit_glow_import.gd) and the texture .imports pinned mipmaps-on;
- a roster .tres with donor stats, EMPTY moves/abilities/AI, and PLACEHOLDER in the description + a comment;
- the unit added to CharacterSelect.EXCLUDED_IDS, attached to NOTHING else;
- sized by the NEW true-size path (whatever e8974f2 + the import skill say: roster model_scale for the design
  height, no cell-fit), at seedant 1.067 m (3 ft 6 in). shadowbun has no artist size yet: use its size-table
  target (1.801 m) and flag it as an open question in the report.
Pick the biome folder from the existing ones (seedant = plant/forest, shadowbun = dark/shadow; say which and
why), and the donors from same-role units. Shadowbun's eye + grin glow (Glow 0.4) must survive the import (the
glow lines in the import log). Both units must pass the -Y facing check.

FILES (Conquest allowlist): game/characters/models/<biome>/seedant_forge*, .../shadowbun_forge*,
game/characters/roster/seedant.tres + shadowbun.tres, the CharacterSelect EXCLUDED_IDS line, and
tools/blender/assets.conf ONLY IF the import skill requires a row for new units. Everything else in Conquest
is READ-ONLY: it carries the user's own active work (story, HeroMover; an untracked tests/results/junit.xml
exists, leave it). Forge is read-only too. Scope: never sweep, never revert, never reformat.
Tools: Godot headless only
(C:\Users\kenne\Downloads\Godot_v4.6-stable_win64.exe\Godot_v4.6-stable_win64_console.exe --headless ...); the
glb probe harness is forge projects/conquest-units/survey/color1_probe/probe_project. Blender headless only.

EVIDENCE: Deliverable 1's table; the import log (glow lines for both units); facing check results; the
headless suites: tests/unit/test_true_size_units.gd, the roster GUT suite, the select + compendium suites
(numbers as N/M); grep proving both new ids are referenced only by roster + models + EXCLUDED_IDS; the list of
files changed (git status --short, Conquest).
STOP: done = both deliverables + evidence. Blocked = the true-size path cannot size a NEW unit without
editing code outside the allowlist (report the exact gap). Grown = a sizing-system fix is needed (report it,
don't do it). Never commit (the orchestrator commits, scoped), never spawn agents, never launch anything
windowed (no game window, no browser pane, no GUI Blender/Godot), never touch port 9876.
