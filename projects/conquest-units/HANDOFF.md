# Conquest-units session handoff (rewritten 2026-10-04; supersedes the 10-02 version)

Repo current at the commit carrying this file; everything below is also in git history.
No lanes in flight.

## Read first (in order)
1. docs/lane-conventions.md — the law (lane contract, context economy, two speeds,
   edits-vs-rebuilds, measurement conventions).
2. design/character-style-guide.md — THE HOUSE STYLE, now including: mass-first
   sheet-matched hair, beard shells (crop + long), stance/IK laws, HAND_POSES,
   loop-key gates, trunk-hung garment rules, clipless bind-pose law.
3. design/OPEN-QUESTIONS.md — EVERY pending artist decision, numbered (W/E/V/G),
   each with its live default and the exact knob/file. The artist hands these to
   agents; an agent picking one up changes the knob, reruns the unit's runner,
   renders, and reports.
4. design/review-log.md — artist verbatim, binding; newest entries carry the queue.
5. Per-unit: improve/<unit>_STATE.md (wren, elias, varden are rich; others thinner).

## State of the world (2026-10-04)
- ALL 18 MODELS ARE IN THE GAME on Conquest branch feat/forge-unit-import (user
  merges): 9 battle units on forge models, Wren as the overworld hero (male — never
  "girl"; story hero), and 6 creatures + Elias + Varden as COMPENDIUM PLACEHOLDERS
  (donor stats, empty moves/AI, CharacterSelect.EXCLUDED_IDS, "PLACEHOLDER" in the
  description). Compendium = 20 entries.
- Delivery pattern (copy it for any new unit): glb -> Conquest
  game/characters/models/<biome>/<unit>_forge.glb + .import (root_scale =
  min(1.8/h, 1.9/fp), import_script unit_glow_import.gd), roster .tres
  (model_scene, model_scale 1.0, yaw 0), texture .imports pinned mipmaps-on.
  Precedents: commits 287ff9f (the 6), 9a90d02 (elias), 01b5b2e (varden).
- Glow: float _GLOW vertex attribute end-to-end (COLOR_1 clamps >1.0); Conquest's
  addons/color1 recovers it as CUSTOM0 (morph-safe); emissive palette values <= 2.0
  (phone cap). All 16 creature glbs + self-exporting builds emit it.
- Game feel: F7 cycles look presets (warm grade etc.), F8 cycles overworld feel
  (the user's own HeroMover "free" movement is now default; speeds 2.2/5.0 m/s).
  Hero clips loop natively (OverworldActor runtime LOOP_LINEAR copies).
- Wren locomotion is current: fists, knee 25deg, straight legs, double arm swing,
  hip pouch, sprint ("young hero sprint"), true-period loop keys, no pitchfork
  (HAS_FORK knob; weapons ONLY in combat scenarios — binding).

## Operating facts (unchanged + new)
- Units under projects/conquest-units/ (improve/ scripts+runners, improved/ +
  rigged/ outputs, palettes/, renders/, source-copies/ READ-ONLY).
- One-command runners improve/<unit>_run.ps1, headless only. Blender:
  "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background
  --factory-startup. Godot (headless gates):
  C:\Users\kenne\Downloads\Godot_v4.6-stable_win64.exe\Godot_v4.6-stable_win64_console.exe.
  Port 9876 = live scene, never. NEVER launch anything windowed; the user launches.
- Orchestrator gates + commits scoped per lane; push backup master. NEVER double
  quotes inside -m here-strings (PS arg parsing; burned repeatedly). OneDrive makes
  big commits slow — background them.
- Laws: never pay; agents never commit/spawn; WIP <= 2 implementation lanes,
  disjoint files; implementation on Opus. Kiro CLI is a secondary agent pool for
  mechanical/audit lanes (brief-file + one-line pointer dispatch; it throttles and
  has a 1h background ceiling — keep long-verification lanes on Claude).
- The CONQUEST working tree carries the user's own active work (story content,
  HeroMover) — scope every git add precisely; never sweep.
- The probe/test harness for glb questions lives at survey/color1_probe/
  probe_project (drop a glb in, --import, dump script pattern).

## Queue (artist-ordered)
1. OPEN-QUESTIONS items as the artist hands them out (each is lane-ready).
2. TOWN HOUSE FAMILY — the last big gameworld piece: 3-5 silhouette variants,
   1-3k tris, beveled edges, vertex-AO, runtime tint; integration =
   OverworldProps.prop() match (~:155). Spec: survey/conquest_town_assets_audit.md
   + design/research/godot-world-feel.md (budget rec + dressing rules). Then
   chapel/smithy, NPC archetypes.
3. Deferred: attack/hit/death clips (movement intents mostly unasked — ask per
   unit, batch with renders); skins delivery shape; vampwarrior toon + magmoo
   translucency wiring (import-notes items 5/6); NPC stride data; werewolf
   delivery lane (separate project contract).

## Research on file (both actionable, partially mined)
- design/research/hair-face-best-practices.md — remaining unapplied items: aging
  rework (nasolabial stroke set + iris-by-age), lengthwise lock UVs + ramp,
  post-resolve lock union (needs determinism trial), gameplay-size head panel on
  sheets.
- design/research/godot-world-feel.md — remaining: unit rim light, cloud shadows +
  colored bounce, tilt-shift/vignette (behind quality setting).
