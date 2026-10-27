# Handoff: import Duskmaw (base + true form) into Conquest and Dungeon Diver, 2026-10-26

Artist (verbatim): "import duskmaw into the two games dungeon diver and conquest, coordinate with agents from those
projects and hand off to them, the true form duskmaw is a form shift on ultimate".

## The models (forge, on disk; forge has NOT committed them yet)

Forge repo: C:\Users\kenne\OneDrive\Desktop\git\forge. Paths below are under projects/conquest-units/.

| model | file | forge build | glb sha256 (first 16) |
|---|---|---|---|
| Duskmaw base | rigged/duskmaw.glb | v6.2, DIGEST 12e32496b661a1c1 | 200297374279f6d4 |
| Duskmaw true form | rigged/duskmaw_true_form.glb | v1.1, DIGEST 46e5e5268b5132c1 | b635d7e1b55cf7c1 |

- Check the sha before copying. If it differs, forge rebuilt the file: ask the forge session before importing.
- Renders to look at:
  - renders/duskmaw/duskmaw_v62_panel_front.png (sheet | v5.4 | v6 | v6.2, open + closed);
  - renders/duskmaw/duskmaw_v62_panel_mouth.png;
  - renders/duskmaw_true_form/;
  - the clips in renders/duskmaw*/ (*.mp4).
- Base v6.2: the clean re-run body with v5.4's open maw grafted in (artist: "duskmaw is fine for now"). Known
  follow-ups, not blocking: the teeth read too cream, the torso is boxy around the maw, the throat is busy. The true
  form v1.1 has not had an artist review as a model.
- The two forms are TWO SEPARATE MODELS, swapped under a VFX. They are not a morph.

## Clips (all PLACEHOLDER: movement is still unanswered, OPEN-QUESTIONS D1)

Base duskmaw.glb (31 bones):
- `idle`, 97 f, loops: the torso maw alternates closed (a glowing tear) and open (a wide toothy maw).
- `walk`, 49 f, loops: the GLIDE (answered 2026-09-25), mouth closed.
- `close`, 13 f, one-shot.
- `transform_out`, 49 f, one-shot: it sinks into its tendrils and the maw opens. The LAST frame is the swap point. It
  dips below the floor by design (min z -0.78 u) just before the swap.

True form duskmaw_true_form.glb (19 bones):
- `idle`, 73 f, loops.
- `walk`, 33 f, loops.
- `chomp`, 25 f, loops.
- `close`, 17 f, one-shot.
- `transform_in`, 49 f, one-shot: it rises out of its tendrils. The FIRST frame follows the base's swap.

Frame rate: 24 fps (idle 97 f = 4.0 s).

## The mechanic (artist decision, 2026-10-26)

The true form is a FORM SHIFT on Duskmaw's ULTIMATE:
1. The base plays `transform_out`.
2. At its last frame, swap to the true-form model under a VFX (the game's own effect).
3. The true form plays `transform_in`, then its own idle / walk / chomp for the ult's duration.
4. Then it shifts back.

Each game decides the ult duration, the shift-back and its stats from its own kit (Conquest's kit / evolutions,
Dungeon Diver's champion abilities). There is no `transform_back` clip. To shift back, reuse the VFX swap, e.g.
play true-form `close` -> swap -> base `idle`. If a dedicated shift-back clip is wanted, ask forge.

## Sizes

- Base: keep each game's current in-game size for Duskmaw. Conquest pinned 1.80 m in game (root_scale 0.04316,
  game/characters/roster/monster.tres; forge size-table.json). Dungeon Diver uses height 2.3 (champion) and 6.5
  (monster) in src/data/champions.gd / monsters.gd.
- True form: the artist has not given a size yet (OPEN-QUESTIONS D2: the sheet's scale panel draws it ~2.5x a human).
  Use ~2.5x the base form's in-game height as a placeholder, and report it as one.

## Import notes (forge side)

- Emission: glowing regions (eyes, teeth, tear) are palette regions with emission. Import the same way the current
  duskmaw_forge.glb was imported in each game, and check in-game that the eyes / tear / teeth glow and nothing else
  does. Conquest: design/godot-import-notes.md item 1 (COLOR_1 / emission).
- The new glb REPLACES each game's existing duskmaw_forge.glb. Keep the game's existing file / resource names unless
  your project's conventions say otherwise.
- Clip names changed versus the old procedural duskmaw (it had idle / walk only). Wire `transform_out` /
  `transform_in` into the ultimate.

## Coordination

- Each game's import session works in ITS OWN repo, in a worktree / branch. Both repos have someone else's
  uncommitted work on their current branch; do not touch it.
- Questions about the models go to the forge session that started you (it appears as "Model reference pattern" when
  you list agents). Questions about the game go to the game repo's own docs and owners.
- Commit on your branch in your repo. Do not push and do not merge without the artist's word. Report back the branch,
  what changed, in-game screenshots of both forms and the shift, and anything forge must fix.
