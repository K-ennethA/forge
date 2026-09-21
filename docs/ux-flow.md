# Forge UX flow — the beginner's path through the tool

Owner-directed (2026-09-20). The principle: a person who has never opened
Blender describes what they want, shows pictures, and watches it get built —
making calls at the moments that are theirs to make. Three screens, and at any
moment exactly one of them is in front of you.

## Screen 1 — Home / Library

The app opens here. Two things on it, nothing else:

**Start something.** One prompt box ("a werewolf character for my game", "a
bracket to hold my router to the desk") and five workflow cards: Character ·
3D-Print Part · Device · Floor Plan · Mold — the five task chains that already
exist. Typing can imply the workflow but never silently: the matching card
lights up and the person confirms with a click (the config-map law — select,
don't make the model guess). References can be dropped straight onto the
prompt.

**The library.** Every project as a card: thumbnail (newest render), name,
workflow badge, progress ring off its real build-plan ("7/10"), last touched.
Click → that project's Studio, exactly where it left off. A third, quieter
entry: **"Add Forge to an existing .blend"** — pick a file, Forge wraps a
project around it (its mesh enters the pipeline at verify_mesh; no generation
stages pretend to have run).

## Screen 2 — Planning room (new project, or any time from the Studio)

**The reference board.** Drop photos. Each gets a type tag — Front / Back /
Side / Floor plan / General — and an optional plain-text note ("this jacket,
but longer"). Tags are data: they drive the multiview conditioning (the
back-reference lesson: an untagged pile of images is why the model's back was
a guess). Notes go to the assistant verbatim. Stored as
design/refs/ + a refs-manifest.json (tags, notes, which prompt added them).

**The plan, beside it.** The design conversation (the existing at-most-five-
questions rule) fills the config sheet, rendered as friendly cards — the sheet
already self-documents every knob in plain words with a why. For a 3D print,
the part template's spec fields (dimensions, clearances, material) appear
here, and the assistant asks for the missing ones during planning instead of
discovering them mid-build.

**"Start building"** materialises the pipeline and opens the Studio.

## Screen 3 — Project Studio (one project, one pipeline, no clutter)

Today's workspace, kept and simplified — this screen already exists:
stepper → focused stage → live model → docked chat, drawers for
renders/versions. What changes for beginners:

- Every stage chip carries a **plain-word name and a one-line "what happens
  here"**. The focused stage answers three things in order: what was measured
  (in words), what it looks like (render / heatmap / pins), and **the big
  three actions**: *Looks good* (advance) · *Fix these* (the findings pins) ·
  *Let me adjust* (the direct-edit tier: nudge, sliders, brush).
- **A scoped change box** on the focused stage — "make the boots chunkier"
  typed at the rig stage is a stage-scoped turn, not a global one.
- **Select an area to improve**: click or lasso a region on the live model;
  the selection travels with the next prompt or direct edit as a world-space
  region ("improve THIS"). Builds on the viewer's existing pick machinery.

Navigation is Home ↔ Studio. The old top-level tabs fold in: Flows become
stage actions where they belong; the old free-floating chat becomes the
Studio's docked chat; Library IS the home screen.

## Build lanes (contract-sized, in order)

1. **Reference board + creation flow** — refs upload/tag/note endpoints +
   manifest; Home's prompt-plus-cards; the planning room shell. (bridge +
   webui)
2. **Library home** — project cards with real progress rings, resume,
   attach-to-existing-blend. (bridge + webui)
3. **Studio beginner pass** — stage plain-naming, the big-three actions,
   scoped change box. (webui, mostly relabeling + layout of what exists)
4. **Region select → improve-this** — lasso/click region payload wired into
   prompts and direct edits. (webui viewer + one bridge endpoint)

Meshgen conditioning reads the manifest tags (front/back/side) instead of
guessing image roles — that closes the loop the back-texture debt opened.
