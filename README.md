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
meshgen/       Image-to-3D service (HTTP, port 8902): picture in, textured .glb out, swappable model backends
mcp/           MCP server (stdio) exposing both to Claude Code
templates/     spec.json / printer.json / character.json starting points
projects/      One folder per real part or character
docs/          Plan + architecture contract
start_forge.cmd / stop_forge.cmd   Double-click launchers for the two background programs
```

## Setup (done on this machine)

Already in place here: printer profile (Elegoo Centauri Carbon), venvs for `service/` and `mcp/`, the add-on installed into Blender with server autostart, and the MCP registration via `.mcp.json`. One-time remaining step: sign the Claude CLI in (`claude` in a terminal, then `/login`) so the Assistant chat box can answer.

Fresh machine: fill `templates/printer.json`, create the two venvs (`pip install -e .` in `service/` and `mcp/`), install `addon/forge/` into Blender, run `claude` → `/login` once, then double-click `start_forge.cmd`.

## Status

Nineteen phases built and headless-verified (see docs/architecture.md, the living contract): parametric parts with panels, maker/electronics designs with BOMs, image-to-3D (single and multi-view, tuned under a geometric verifier), silhouette fitting, rigging + deformation gates, mechanism demos, floor-plan-to-level with incremental rebuilds and manual-edit absorption, live Blender context for the assistant, and a POV playtest camera. ~2,600 pytest items + ~300 headless Blender checks. Historical plan: docs/plan.md (stamped; superseded by architecture.md).

---

Image-to-3D in Forge is **Built with DINOv3** (Meta AI); its license requires this attribution. Generation runs locally via ComfyUI (GPL, arms-length over HTTP) with MIT-licensed TRELLIS.2 and Pixal3D weights.

