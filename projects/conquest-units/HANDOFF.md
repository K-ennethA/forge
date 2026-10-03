# Conquest-units session handoff (written 2026-10-02 for a fresh orchestrator session)

Everything below is also in git history; this is the fast-start index. Repo is current
and mirrored at the commit carrying this file. No lanes are in flight.

## Read first (in order, ~4 files)
1. docs/lane-conventions.md — the law (lane contract, context economy, BVH tie-break,
   bake tolerance pattern, two speeds, edits-vs-rebuilds).
2. design/character-style-guide.md — THE HOUSE STYLE (Wren = reference implementation).
3. design/review-log.md — artist verbatim, binding; newest entries carry the queue.
4. design/godot-import-notes.md — the 6 import-wave items.
Per-unit facts: improve/<unit>_STATE.md where present (wren pilot); otherwise the unit's
build script docstring + latest check jsons.

## Immediate queue (artist-ordered, 2026-10-02)
1. IMPORT WAVE: dispatch a Conquest-side audit agent first (READ-ONLY on
   C:\Users\kenne\OneDrive\Desktop\git\Conquest): map game roster <-> the 16 built units
   (rigged/*.glb), list wiring needs per godot-import-notes (glow shader, translucency
   prepass, scales, yaws incl duskmaw's stale 180, skin delivery shape), and list game
   characters with NO sheet and NO forge model (artist will supply designs). THEN the
   import lane: additive work on a NEW branch in git/Conquest (never modify their source
   assets; user merges). Werewolf drop-in contract is the pattern.
2. NEW UNIT: Professor Elias — full sheet transcription in the review-log entry of
   2026-10-02 (sheet image was inline-only; transcription is binding). Build per the
   style guide from the first pass; hero tier; staff/book as own-bone props.
3. Open artist verdicts: see each unit's STATE/questions in the review log (wren's in
   wren_STATE.md; magmoo scale question; supaoctto emblem polish; firefly/firesprite
   tuning knobs; geode/vampito/eldroot smalls).

## Deferred waves (artist-gated)
- Attack/hit/death clips (designs recorded: vine-blade merge, magmoo puddle death,
  firesprite casting). Werewolf delivery lane (werewolf project, separate contract).
- Queued grown items: wren +45-lock hair density (needs ~2.5k harvest); wren
  fringe-on-skin discontinuity; game-side toon shader prototype.

## Operating facts
- Units all live under projects/conquest-units/ (improve/ scripts+runners, improved/ +
  rigged/ outputs, palettes/, renders/, source-copies/ READ-ONLY byte copies).
- Every unit has a one-command runner improve/<unit>_run.ps1 (hidden, headless,
  twin-determinism + tolerance gates). Blender: "C:\Program Files\Blender Foundation\
  Blender 5.0\blender.exe" --background --factory-startup. Port 9876 = live scene, never.
- Commit style: orchestrator gates then commits scoped per lane; push backup master;
  NEVER double quotes inside -m here-strings (breaks PS arg parsing - burned 3x).
- Laws: never pay; nothing windowed (browser pane = orchestrator only); agents never
  commit/spawn; WIP <= 2 lanes, disjoint files; implementation lanes on Opus.
- Third-party references manifested in C:\forge-assets\thirdparty\MANIFEST.md
  (Alicia Solid VRM = study-only).
