LANE: SIZE LINEUP — every Conquest unit standing in a row, one floor, a height ruler, rerunnable.

LAW: docs/lane-conventions.md (draft speed; nothing windowed; never port 9876; never commit or spawn).
SPEC: projects/conquest-units/design/review-log.md entry "2026-10-09 — NEW UNIT: Shadowbun ... + size lineup"
item (3), and design/OPEN-QUESTIONS.md "Size lineup" Z1. Artist (verbatim): "sizing would help to see our all
conquest creatures in a line so can compare and give reference to size".
Paths below are relative to projects/conquest-units/.

DELIVERABLE: a reusable lineup renderer that shows the artist the RELATIVE SIZES of every unit and gives them
one file to edit sizes in:
  improve/lineup_render.py   headless Blender script: discovers every rigged/<unit>.glb (skip <unit>__<skin>
                             variants), imports each into one scene (bind/rest pose, or idle frame 1 when the
                             unit has clips, whichever the game shows standing), scales it, stands it on z = 0
                             facing -Y, and lays them out in a row with a gap ~0.25 x the tallest height.
  improve/lineup_run.ps1     one command (hidden, headless) -> renders/lineup/*.png + a json table.
  design/size-table.json     ONE ROW PER UNIT: {"unit", "kind" (creature / humanoid / boss), "ingame_height_m"
                             (measured, see below), "target_height_m" (default = ingame_height_m), "note"}. The
                             artist edits target_height_m. The renderer never overwrites an edited target (merge:
                             new units appended, existing rows kept).
Renders (ortho FRONT, a light ground grid, a metre ruler with 0.25 m ticks at both ends, every unit labelled
with name + height in m; sorted by height, plus a roster-order variant; one perspective three-quarter version
for reading forms):
  renders/lineup/lineup_ingame.png   every unit at TODAY'S IN-GAME scale, i.e. what the player sees now
  renders/lineup/lineup_target.png   every unit at size-table target_height_m (identical until the artist edits)
  renders/lineup/lineup_ingame_tq.png
  renders/lineup/lineup_table.json   unit -> natural bbox (units), ingame scale + height/footprint m, target

IN-GAME SCALE is the first thing to MEASURE, not assume. HANDOFF.md says each glb is delivered to the Conquest
game with an .import root_scale = min(1.8/h, 1.9/fp) and a roster .tres model_scale (1.0). Find the Conquest
working tree on this machine (read-only: look for game/characters/models/*/*_forge.glb(.import) and the roster
.tres files; HANDOFF.md / design/godot-import-notes.md name branch feat/forge-unit-import), and read the ACTUAL
root_scale and model_scale per unit. Units not yet in the game (seedant, shadowbun, any newer ones) get the
formula's value, and the table marks them "not in game: formula". If the Conquest tree cannot be found, use the
formula for all units and say so. Report the measured vs formula scale per unit. Most regular units are
cell-fit, so expect them near-uniform in-game height: SAY SO PLAINLY in the report, because that is the finding
the artist needs.

FILES (allowlist): improve/lineup_render.py, improve/lineup_run.ps1, design/size-table.json, renders/lineup/*,
improve/log_lineup_*. Everything else is READ-ONLY. That includes every <unit>_build.py, every rigged/ file,
and the whole Conquest repo. Another lane (seedant) may be rewriting rigged/seedant.* while you work: read
glbs only, and if seedant's glb changes mid-run just rerun at the end. SCRATCH: <session scratchpad>/lineup_lane/.

EVIDENCE: the run command + wall time; the unit count discovered vs rigged/*.glb; the per-unit table (natural
height in build units, in-game scale (measured | formula), in-game height m, footprint m); your read of
lineup_ingame.png (which units read too big or small relative to their role, framed as observations for the
artist, not decisions); and how to add a unit / edit a target (two lines).
STOP: done = the three renders + json + size-table written. Blocked = the glTF import or framing needs an edit
outside the allowlist (report it). Grown = anything that resizes a unit's actual build or delivery (that is a
follow-up per unit, NOT this lane).
