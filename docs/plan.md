# Custom AI 3D Pipeline — Plan

Two tools, one brain. Both are add-ons we own, both are driven by Claude through a local socket (MCP), and both are built on top of free, open-source foundations so nothing in the stack costs money beyond the AI itself.

## 1. Cost and licensing

Everything in the proposed stack is free for your use:

Blender (GPL, free). Blender MCP by ahujasid (MIT, open source; we fork it and add our own tools rather than start from zero). Rigify and the Cloth simulator (ship inside Blender). Build123d / CadQuery (Apache 2.0 Python CAD kernels, produce real solids and export STEP/STL/3MF). Bambu Studio / PrusaSlicer / OrcaSlicer (free slicers with command-line interfaces we can call). Godot (MIT). Game Rig Tools (free Blender add-on for converting a Rigify control rig into a game deform rig). Mixamo (free animation library for retargeting, requires an Adobe account). Fusion 360 stays optional: the personal license is free for hobby use and the community MCP bridges are open source, but the plan below does not depend on it.

The only recurring cost is Claude usage (Claude Code or the API). If you want that to be zero too, the MCP layer is model-agnostic, so a local model through Ollama can drive the same tools, with a quality drop.

## 2. Architecture

Claude Code is the orchestrator. It reads your description and any reference images, holds the "spec" for what you are making, and calls tools. It does not hand-edit geometry; it writes and runs code against tools we control.

A single Blender add-on (working name **Forge**) exposes two tool families over the MCP socket: **PartForge** for parametric printable parts and **RigForge** for characters. Blender is the viewer and the place you turn knobs for both. A separate Python geometry service runs Build123d for the CAD kernel, because Blender's own mesh tools are the wrong kernel for dimensioned, watertight, print-ready solids.

Every project gets a folder with a `spec.json` (what it is, the parameters, the constraints), the generated script, exports, and preview renders. That file is the contract between you and Claude: you can edit it by hand, Claude can regenerate from it, and it is versioned in git.

## 3. PartForge — describe it, get a configurable printable model

**What you experience.** You write a paragraph ("a holder that wraps a 6 in stainless bowl, decorative textured band around the bottom, four feet, two slot-in ears") and optionally drop a reference image. Claude replies with a short spec sheet: named parameters with proposed values, features, print constraints. You confirm or correct. Claude generates the part, it appears in the Blender viewport with a panel of sliders and fields (bowl diameter, wall thickness, lip height, number of segments, joint tolerance, feet count), and every change regenerates the solid. When you are happy you press Export and get one STL/3MF per segment plus a STEP for Fusion.

**How reference images are used.** Claude looks at the image to extract proportions, feature list, and style intent into the spec. Geometry is always built from parameters, never traced from pixels, which is what keeps the model editable with real values. The image can also be loaded as a background plane in Blender so you can eyeball proportions against it.

**Geometry generation.** Claude writes a Build123d script with a `PARAMS` block at the top. Each parameter is typed (mm or in, min/max, description). The add-on reads that block and turns it into the Blender panel automatically, so any script Claude writes gets a UI for free. The service runs the script, tessellates the solid, and streams the mesh to Blender. Regeneration on a slider change takes a second or two for parts of this complexity.

**Printer awareness.** `printer.json` stores your bed size, nozzle, layer height, material tolerances, minimum wall and minimum feature size. Before export the tool runs checks: fits the bed (else auto-segment), no walls thinner than minimum, overhang report with suggested orientation, watertightness. Results appear in the panel with a pass/fail per check.

**Segmenting.** Ring-shaped parts are cut radially into N equal arcs; tall parts are cut on planes. Each cut gets a joint you pick from a library: dovetail, pin-and-socket, or magnet pockets, with a tolerance parameter tuned to your printer. Segments are exported as separate files and also as one 3MF plate laid out to fit the bed. Slicer profiles can be applied from the command line so the output is a ready-to-print project file.

**Appendage slots.** Decorative pieces (ears, tail, feet, fins, whatever the creature is) are separate parts with a standardized keyed peg that mates with a socket generated on the base. Change the peg diameter once in the spec and every socket updates.

**Mold mode.** Flip one switch and the tool produces a mold master instead of the final part: draft angles applied to vertical faces, a two-piece mold box with a parting plane you can drag, registration keys, a pour spout and vents. The negative is printed, then silicone or resin is cast. Same parameters drive both the direct-print and the mold versions.

**Roles.** You: describe, confirm spec, tweak sliders, print. Claude: extract spec, write script, respond to "make the lip taller" by editing the script rather than the mesh, run checks, explain failures. Tool: turn scripts into solids, panels, checks, exports.

## 4. RigForge — sculpt by hand, let the tool do the pipeline

**Semantic tagging is the foundation.** Everything downstream depends on Blender knowing what the parts of your sculpt are. The add-on gives you a tag panel: select faces, click "Head", "Torso", "Arm L", "Ear L", "Tail", or type a new tag. Tags are stored as vertex groups plus a `character.json` manifest that also records archetype (biped, quadruped, creature with N limbs) and motion notes you write in plain language ("ears are floppy and lag behind head", "tail drags on the ground", "hops rather than walks"). You can also tag by describing: "the two lumps on top are ears" lets Claude select by position and propose the tag for you to accept.

**Stage 1 — Sculpt.** Untouched. You sculpt.

**Stage 2 — Retopology.** One click produces a game mesh from the sculpt: voxel remesh for a clean base, Quadriflow (built-in, free) for quads at a target face count, then shrinkwrap to the sculpt and bake normals from high to low. Target polycount is a parameter per platform (desktop, mobile). Tags transfer automatically to the new mesh by proximity. Optional decimated LOD1 and LOD2.

**Stage 3 — UV and materials.** Auto seams derived from the tags (seam at neck, shoulders, wrists), unwrap, pack. You can override in Blender's normal UV editor.

**Stage 4 — Rig.** The tool places a Rigify metarig using the tags: it measures each tagged region's bounds and landmarks (head top, chin, shoulder, elbow, wrist, hip, knee, ankle) and scales and positions the metarig to the sculpt. Archetype picks the template (biped, quadruped, or a custom assembly for odd creatures, built from limb, spine and tail modules). Ears and tails become bone chains with spring/jiggle constraints per your motion notes. Rigify then generates the control rig for animation. Auto weights are applied, then per-tag cleanup rules (no head weights below the neck tag, and so on). You fix weights by hand where needed; nothing is locked.

**Stage 5 — Clothing.** Select a region or say "give it a short-sleeved shirt and shorts": the tool duplicates the tagged faces, offsets them outward, adds thickness and a Cloth modifier with a preset (cotton, leather, heavy) and a collision on the body. Two output modes: simulated cloth baked to shape keys or bones for game use, or skin-tight cloth simply weighted to the same rig. Godot does not run Blender cloth, so the bake is what ships.

**Stage 6 — Animation.** An action library with names Godot expects (`idle`, `walk`, `run`, `jump`, `attack`, plus a `-loop` suffix convention). Three ways to fill it: describe the motion and let Claude keyframe it on the control rig ("a heavy two-beat hop, ears trail"), retarget a Mixamo or other mocap clip, or hand-animate. Motion notes from the manifest are applied as secondary motion automatically (ear lag, tail follow). Everything lives in the NLA so clips can be layered and reviewed.

**Stage 7 — Godot export.** Game Rig Tools converts the Rigify control rig to a deform-only rig, bakes all actions onto it, then exports glTF with Godot conventions: unit scale, applied transforms, Y-up, root motion bone if requested, actions named and looped, LODs and collision meshes with `-col`/`-lod` suffixes. A companion Godot import script (or `.import` settings) is emitted so the character lands as a scene with AnimationPlayer or AnimationTree ready. Round trips are cheap: change the sculpt, rerun stages 2 to 7 from the manifest.

**Roles.** You: sculpt, tag, describe motion, polish weights and animation. Claude: interpret descriptions, choose stage settings, write custom keyframe passes, explain problems. Tool: everything deterministic.

## 5. Build order

Phase 0, setup (a session): install Claude Code, fork Blender MCP, stand up the Build123d service, create `printer.json` from your printer's specs, agree on the `spec.json` and `character.json` formats.

Phase 1, PartForge MVP: PARAMS-to-panel, live regeneration, STL/STEP export. Test on the bowl holder base ring and collar band.

Phase 2, PartForge print readiness: printer checks, segmenting with joints, appendage slots, 3MF plate, slicer CLI. Then mold mode.

Phase 3, RigForge foundation: tag panel, manifest, retopo, auto UV.

Phase 4, RigForge rig and export: metarig placement from tags, Rigify generation, weight cleanup, Game Rig Tools conversion, glTF export, Godot import script. This is the point where a sculpt becomes a playable character in one run.

Phase 5, RigForge cloth and animation: garment generation and baking, action library, described-motion keyframing, mocap retargeting, secondary motion.

Each phase ends with you running the tool on a real project, not a demo, and we adjust the plan from what breaks.

## 6. Open decisions for you

Which printer and slicer you use (determines `printer.json` and which CLI we wrap). Whether Fusion stays in the loop for finishing or STEP export from Build123d is enough. Whether you want Claude Code in a terminal next to Blender, or a chat panel inside Blender (the latter is more work; the former is where I would start). Target platform for Godot characters (desktop only, or mobile, which changes polycount and bone budgets).
