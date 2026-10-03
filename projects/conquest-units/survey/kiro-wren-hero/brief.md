# Lane brief: wire the forge WREN model as the hero avatar in Conquest

Law: C:/Users/kenne/OneDrive/Desktop/git/forge/docs/lane-conventions.md — read first; binding. Headless only, NEVER open the Godot editor or any window, no commits, no pushes, never pay. Work on the CURRENT branch of C:/Users/kenne/OneDrive/Desktop/git/Conquest (feat/forge-unit-import — verify with git branch --show-current; if it differs, STOP and report).

ARTIST DIRECTIVE: "the hero avatar is the wren model we made." The story hero currently uses a placeholder (vineweave.glb at model_scale 1.3 per HeroResource/hero.tres — the import audit at forge/projects/conquest-units/survey/conquest_import_audit.md section on wren has the details).

PATTERN TO COPY EXACTLY (9 units already done this way — read these first):
- forge/projects/conquest-units/survey/import_wave_report.md — the delivery shape: glb copied to game/characters/models/<biome>/<name>_forge.glb, a .import file setting root_scale for cell fit and the importer script game/visuals/units/unit_glow_import.gd, roster/.tres pointing model_scene at it, model_scale 1.0, yaw 0.
- An existing example pair to mirror: game/characters/models/earth/geode_forge.glb + geode_forge.glb.import and gem_knight.tres.
- The white-unit fix is already on the branch (self-healing glow import); do not re-implement anything, just follow the pattern.

TASK:
1. Copy forge/projects/conquest-units/rigged/wren.glb (read-only source) to the fitting biome/story dir as wren_forge.glb with a .import file per the pattern. Scale: root_scale so he stands a plausible human-hero height in-game (the 9-unit table used 1.80 m for humanoid heroes; wren's natural height is in rigged/wren.json — compute root_scale the same way geode/mortis did).
2. Point the hero resource (hero.tres / HeroResource) model_scene at wren_forge.glb, model_scale 1.0, yaw 0. Check every consumer the audit lists (portrait, preview, overworld) picks it up via the same field — change ONLY the hero resource.
3. GATE (all headless): project --import exits clean with the Color1Ext + unit_glow lines for wren; a dump (reuse the lane's dump pattern from the import report / probe project) showing wren_forge: CUSTOM0 present, emission=false, next_pass=true, vcol_as_albedo=true; run the GUT suites that touch the hero resource (find them by grepping tests for hero.tres/HeroResource) and report pass counts.

FILES allowlist (writes): the new wren_forge.glb + .glb.import (+ any textures Godot extracts beside it), the hero resource .tres, and nothing else. Do NOT touch roster .tres files, project.godot, addons, assets.conf, or the font/junit noise.

Handback <= 25 lines: chosen dir + root_scale + computed height, the dump line for wren, GUT counts, diff stat.
