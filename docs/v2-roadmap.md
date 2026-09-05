# Forge v2 Roadmap — Sketch-to-Blockout (deferred, approved direction)

Status: **approved by the user as v2** (2026-09-05). Not scheduled. Design captured here so it survives; do not build until the user calls for it.

Governing philosophy (applies to all of it): abstract difficulty away — enable the artist without them being an expert on the tech or Blender. Three reply shapes: did it / 90% + beginner handoff / can't-but-here's-how.

## The full artist flow

1. **Sketch** — photo or tablet drawing; front view minimum, front+side better. Attached via the Assistant chat (image attachment field; headless Claude reads images via the Read tool — no new infrastructure).
2. **Sketch reading, not tracing** — Claude extracts a `character_sheet.json`: parts seen, relative proportions, style notes, motion implications ("heavy ears → lag"). Editable JSON, corrected in plain language, same contract philosophy as spec.json.
3. **3D mockup**
   - Hard-surface/printable → existing PartForge lane (already shipped).
   - Characters/organic → **`rigforge_blockout`** (new socket command): character sheet → primitive assembly (spheres/capsules/boxes, positioned/sized per proportions, mirrored where symmetric), **each primitive born pre-tagged** (`tag_Head`, `tag_Ear.L`, …) so it enters the existing RigForge chain with zero tagging work. Plus **`load_reference`** (new command): sketch image → viewport background empty (front/side) for eyeballing.
4. **Iterate** — sheet edits rebuild the blockout in seconds ("ears 30% bigger", "squash the torso"). Cheap-iteration zone.
5. **Handoff** — the honest 90% moment: assistant walks the artist into Sculpt Mode with numbered beginner steps (Tab, Grab, Clay Strips…). Downstream is already one command per stage (retopo → rig → animate → Godot; or checks → segment/mold → print).

## Explicit non-goals / honesty notes

- Claude does not generate or redraw images. AI-improving the sketch itself would be an external image-model integration (cost + accounts) — separate decision, not part of this design. What Claude CAN offer instead: sketch critique, cleaner extracted proportions, SVG turnaround/proportion sheets to sculpt against.

## Build list when v2 starts

- `character_sheet.json` template + schema (templates/).
- Socket commands: `rigforge_blockout`, `load_reference` (additive; contract into architecture.md first, per the established flow).
- MCP mirrors + assistant system-prompt teaching for the sketch flow.
- Assistant panel: image-attachment field (path passed in the message; Claude Reads it).
