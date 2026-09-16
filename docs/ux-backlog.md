# UX backlog — ranked for the artist-first goal

Governing rule: abstract difficulty away; the artist is never required to leave Blender or learn tooling.

Stale-audit 2026-09-15: items 1 and 2 were SHIPPED long ago and are stamped
below so this list stays worth reading.

## Approved next batch (build after the image-reference work lands)

1. ~~**Undo checkpoints for AI actions**~~ — **SHIPPED** (`registry.py` pushes named undo steps for every non-read-only command).
2. ~~**Health row + one-click Start services**~~ — **SHIPPED** (`addon/forge/tools/services.py`, real `/health` polling + hidden starts).
3. **Quick-action chips** — Check printability / Segment to fit bed / Export for printing above the chat box; sends the canned message.
4. **Empty-state guidance** — every panel box with no data states the next step in one plain sentence.
5. **Message queue + cost footer** — queue one pending message instead of 409-busy; session cost total in the panel footer (bridge already reports cost_usd per job).

## Also approved (user, 2026-09-05)

- **"Fix this downloaded model"** — service half IN PROGRESS (/check_mesh, /segment_mesh); addon Import Model button + MCP mirrors in the UI batch.
- **Full-reply viewer** — in the UI batch.
- **Flow editor UI** — modest version in the UI batch: list steps with labels, edit param defaults, reorder/delete steps; full visual authoring stays out of scope (JSON + assistant editing cover creation).

## Deliberately not doing

- Chat streaming into the panel beyond the activity feed (Blender UI redraw cost, low payoff over the ticker).
- In-panel 3D thumbnails for flows/projects (cost > value at this stage).
