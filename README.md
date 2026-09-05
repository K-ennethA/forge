# Forge

## For artists

1. Double-click **start_forge.cmd** in this folder and wait for "Forge is running".
2. Open Blender.
3. Press **N** in the 3D view and click the **Forge** tab on the right.
4. In the **Assistant** box at the top, type what you want in your own words — "split this so it fits my printer" — and press **Send**.
5. When you are done for the day, double-click **stop_forge.cmd**.

---

Custom AI 3D pipeline: two tool families in one Blender add-on, driven by Claude Code over MCP.

- **PartForge** — describe a printable part, get a parametric Build123d solid with a live slider panel in Blender, export STL/STEP/3MF.
- **RigForge** — sculpt by hand, tag semantically, and let the tool run retopo → UV → rig → cloth → animation → Godot export. (Later phases.)

Full plan: [docs/plan.md](docs/plan.md). Component contract (ports, protocols, PARAMS convention): [docs/architecture.md](docs/architecture.md).

## Layout

```
addon/forge/   Blender add-on: TCP command server, common ops (symmetrize, remesh, ...), PartForge panel, Assistant chat box
service/       Build123d geometry service (HTTP, port 8765): PARAMS parsing, tessellation, STL/STEP/3MF
assistant/     Forge Assistant bridge (HTTP, port 8901): runs the Claude Code CLI headless for the chat box
mcp/           MCP server (stdio) exposing both to Claude Code
templates/     spec.json / printer.json / character.json starting points
projects/      One folder per real part or character
docs/          Plan + architecture contract
start_forge.cmd / stop_forge.cmd   Double-click launchers for the two background programs
```

## Setup (not yet done — testing deferred)

1. Fill in `templates/printer.json` with your printer's real specs.
2. Create venvs and install deps for `service/` and `mcp/` (each has a `pyproject.toml`).
3. Install `addon/forge/` as a Blender add-on (zip the folder, or symlink into Blender's addons dir).
4. Register the MCP server in Claude Code using `.mcp.json` at the repo root.

## Status

Phase 0–1 in progress: scaffolding, add-on core + common ops, geometry service, MCP bridge, PARAMS→panel. Untested until first manual test session.
