"""Forge MCP server — one stdio tool surface over both Forge backends.

* Blender add-on socket (127.0.0.1:9876) — scene inspection, common mesh ops,
  mesh loading, STL export.
* Build123d geometry service (127.0.0.1:8765) — PartForge parametric parts.

Protocols and command names are fixed by docs/architecture.md; the tools here
are thin, well-labelled wrappers over them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from mcp.server.mcpserver import MCPServer

from . import (
    __version__,
    blender_client,
    config,
    floorplan,
    maker,
    meshgen_client,
    pipeline,
    service_client,
    task_config,
    util,
)
from .errors import BackendError, BackendUnavailable, ForgeError
from .util import (
    GLTF_SUFFIXES,
    MOCAP_SUFFIXES,
    PLATFORM_TARGET_FACES,
    action_name_for_clip,
    action_names,
    animation_path,
    derivative_objects,
    design_documents,
    design_filename,
    design_paths,
    ensure_parent_dir,
    flow_document,
    flow_path,
    flow_slug,
    fmt_action_report,
    fmt_animate_report,
    fmt_animation_report,
    fmt_check_in_report,
    fmt_check_report,
    fmt_circuit_plan,
    fmt_cloth_report,
    fmt_component_catalog,
    fmt_counted,
    fmt_design_saved,
    fmt_diagnose_report,
    fmt_emission_report,
    fmt_export_report,
    fmt_floorplan_build_report,
    fmt_floorplan_diff,
    fmt_floorplan_validated,
    fmt_flow_list,
    fmt_flow_run_report,
    fmt_flow_saved,
    fmt_frame_range,
    fmt_generate_report,
    fmt_joint,
    fmt_keyframe_report,
    fmt_manifest_report,
    fmt_merge_report,
    fmt_meshgen_status,
    fmt_metarig_report,
    fmt_mode,
    fmt_model_check_report,
    fmt_model_segment_report,
    fmt_mold_report,
    fmt_new_part_report,
    fmt_number,
    fmt_open_report,
    fmt_outline_report,
    fmt_overrides,
    fmt_params,
    fmt_plan_counts,
    fmt_plunger_plan,
    fmt_preview_report,
    fmt_profile_report,
    fmt_project_open_report,
    fmt_project_save_report,
    fmt_reference_report,
    fmt_retarget_report,
    fmt_retopo_report,
    fmt_rig_check_report,
    fmt_rig_report,
    fmt_scene_info,
    fmt_segment_report,
    fmt_stats,
    fmt_submitted_report,
    fmt_tag_table,
    fmt_turntable_report,
    fmt_undercut_report,
    fmt_uv_report,
    fmt_verify_report,
    fmt_vector,
    fmt_warnings,
    fmt_weights_report,
    fmt_wiring_guide,
    fmt_workspace_report,
    fmt_written_files,
    generated_object_name,
    generated_output_path,
    keys_frame_range,
    meshgen_image_path,
    mold_basename,
    mold_input_path,
    next_rig_step,
    normalize_action_name,
    normalize_animation_fps,
    normalize_animation_frames,
    normalize_animation_resolution,
    normalize_axes,
    normalize_actions,
    normalize_bone_mapping,
    normalize_design_content,
    normalize_emission_color,
    normalize_emission_strength,
    normalize_face_indices,
    normalize_keys,
    normalize_modules,
    normalize_mold_mode,
    normalize_object_keys,
    normalize_poly_budget,
    normalize_preview_objects,
    normalize_preview_resolution,
    normalize_retarget_scale,
    normalize_turntable_resolution,
    normalize_turntable_views,
    normalize_script_source,
    normalize_segment_mode,
    normalize_tag_list,
    normalize_tag_name,
    object_keys_frame_range,
    object_name_for_script,
    ok,
    plate_items_by_name,
    preview_path,
    project_molds_dir,
    project_paths,
    project_slug,
    read_flow_files,
    read_printer,
    read_script,
    reference_path,
    resolve_path,
    rig_objects,
    scene_object,
    spec_document,
    turntable_path,
    update_spec_components,
)

INSTRUCTIONS = """\
Forge drives a Blender add-on and a Build123d geometry service on localhost.

- Scene/mesh tools (get_scene_info, symmetrize, remesh, boolean, ...) act on the
  live Blender session. They target the object named in `object`, or the active
  object when that is omitted. Call get_scene_info first when unsure what exists.
- PartForge tools (partforge_*) work on a parametric Python script with a PARAMS
  block. partforge_generate rebuilds the solid AND pushes it into Blender in one
  call, so "regenerate the part" is a single tool call.
- To change a part's shape, edit its script/parameters and regenerate — do not
  hand-edit the mesh in Blender, it will be overwritten on the next generate.
- When the part does not exist yet, WRITE one: read docs/part-authoring.md, then
  partforge_new_part (validates the PARAMS block through the service and writes
  projects/<slug>/part.py + spec.json) -> partforge_open_in_panel (the sliders
  appear in Blender) -> partforge_generate -> partforge_check. If a check fails,
  revise with partforge_new_part(overwrite=true) and check again.
- When the request is functional, wearable, multi-component or novel, the design
  phase comes FIRST: at most 5 questions in one message, then a requirements
  sheet, a hand-written concept .svg and a components proposal, each saved with
  save_design_doc into projects/<slug>/design/. Geometry starts only after the
  artist signs off on the sheet. A trivially-shaped part skips all of this.
- The design phase ALSO materialises a settings sheet: task_config_init(project,
  task) fills projects/<slug>/design/task-config.json with every knob that kind
  of work has, already at its default, so the artist edits values instead of
  having to know the fields. Settings SETTLE AT READ TIME — task_config_get
  before acting on one, task_config_set to change one, and never carry a value
  the sheet owns in conversation memory. A character's `symmetry` defaults to
  true: bipeds are symmetric unless the artist says otherwise, and asymmetry is
  an explicit choice recorded on the sheet.
- A CHARACTER BUILD IS A STAGED PIPELINE THE ARTIST DRIVES, NEVER A ONE-SHOT:
  "make the mesh", then "check it", then "now rig it". pipeline_status(project)
  is the stage board (reference, design, generate, clean, verify_mesh, rig,
  skin, correctives, animate, export — shorter chains for part/device/floorplan/
  mold); pipeline_advance(project, stage) starts ONE stage and refuses to skip
  over a gate that is red; pipeline_record(project, stage, status, numbers,
  artifacts) is how the turn ends. Do one stage, record its verdict and its
  files, show the artist, and let THEM say go on. A red gate blocks the next
  stage, and stepping over one needs override=true with who and why.
- Print readiness is a pipeline: partforge_check first; if bed_fit fails it
  hands back a `mode` object — pass it verbatim to partforge_segment (planning,
  no meshes), partforge_load_segments (same, plus the pieces laid out in the
  viewport) or partforge_export_segments (files for the slicer).
- A part is designed at the size it SHOULD be. Bed fit is print planning, not a
  design constraint: a bed_fit fail that comes with a feasible split is
  INFORMATIONAL — the report tags it [SPLIT] and says "prints as N pieces" —
  and it never blocks "done" and never justifies shrinking the part. Only an
  INFEASIBLE suggestion is a real problem (too big even segmented), and then
  the options — scale down, or redesign — are the user's to pick.
- A model the artist DOWNLOADED or imported (an STL/OBJ off the internet, their
  own sculpt) has no PARAMS script, so the partforge_* tools cannot touch it:
  check it with check_model and cut it with segment_model, which work off the
  Blender object. Both refuse a mesh that is not watertight — repair that with
  remesh(mode="voxel") first, then ask again.
- Casting is its own lane and its own order: undercut_check FIRST (will it come
  out of a mold at all — and its verdict picks the mode), then make_mold, which
  files the pieces in projects/<slug>/molds/ and hands back the pour
  instructions. Both take a PartForge script, a mesh file, or a Blender object,
  so a generated figure molds exactly the way a parametric part does. Name every
  file the mold wrote in your reply, and say what silicone and resin they still
  have to buy — never buy anything.
- RigForge tools (rigforge_*) take a sculpt all the way to a game character:
  rigforge_tag (once per body part) -> rigforge_retopo -> rigforge_auto_uv ->
  rigforge_metarig -> rigforge_generate_rig -> rigforge_cloth (optional) ->
  rigforge_action / rigforge_keyframe / rigforge_retarget -> rigforge_export_godot.
  rigforge_status is the one-call overview of where a mesh is in that pipeline
  and names the next step; rigforge_weights inspects or repairs the skinning
  between generate and export.
- Animating from a description ("she waves, then folds her arms") is
  rigforge_action to open the clip and rigforge_keyframe to sketch the pose at a
  few frames — not raw Python. rigforge_retarget is for motion-capture files the
  USER supplies (.bvh/.fbx); nothing is ever downloaded.
- Flows are saved, parameterised sequences that replay with no model in the loop.
  Call flow_list BEFORE improvising any multi-step job and prefer a matching
  flow; after finishing a repeatable multi-step job, flow_save it (never a
  single-step one) and tell the artist its name and that Run is in the Flows box.
- A reference image is for extracting features, proportions and style intent
  into parameters — never for tracing. load_reference puts it in the viewport as
  a half-transparent image plane so the artist can compare their model against
  it; offer that whenever they gave you a picture.
- render_preview is how you SEE what you made: it renders the scene to a PNG and
  gives you the path, and you then Read that file. Call it after generating or
  changing anything visual, and always when you worked from a reference picture
  — checks answer "can this be printed", never "does this look right". Compare
  the render against the reference (density, proportions, silhouette, softness)
  and iterate on the parameters if it misses. Never call a visual design done
  without having looked at it.
- THE GATE IS TWO GATES. render_preview judges beauty; verify_design judges
  truth, and BOTH must pass before visual or geometry work is done. Judging by
  eye alone is measurably biased toward prettiness (the same model scores 78 ELO
  higher shown as a splat than as a mesh), so a render loop on its own hands
  over beautiful meshes nobody can use. verify_design is one scored report —
  defects, poly budget, UVs, symmetry residual (reported, never judged), edge
  loops at joints, and silhouette IoU against the artist's reference — and every
  claim in it carries a tier: `measured` (a computed number) or `heuristic` (a
  number that took a judgement call). Quote them apart.
- turntable is the rig for anything organic or generated: 24 views at 256 px on
  one contact sheet, every tile framed identically. A single view hides
  interpenetration, the unmodelled side and the top of the head. Read the sheet.
- ORDER-SWAP LAW: when you compare two renders or two candidates, read them in
  BOTH orders. About a quarter of paired visual judgements reverse when the
  presentation order swaps. If the verdict flips, it is too close to call — say
  so and decide on verify_design's numbers instead.
- generate_3d turns a picture into an actual mesh (meshgen, ~5 minutes, one job
  at a time) and is the right first offer for ORGANIC, stylised, one-off shapes
  — a creature, a bust, an ornament. Anything functional or dimensioned stays
  parametric: a generated mesh has no crisp faces and no exact millimetres. It
  always arrives voxel-repaired and print-checked; say the wait out loud before
  starting, and meshgen_status reports the stage while it runs.
- When likeness is not achievable, deliver STRUCTURE: a sculptable base shape.
  forge_lib.soft_body (a body of revolution from 5-10 (radius, z) points) and
  silhouette_part (an appendage from 6-16 outline points) are the vocabulary,
  and there are three ways in — described, shown in a picture, or DRAWN:
  profile_from_curve and outline_from_curve read a curve the artist drew into
  exactly those control points, so their drawing becomes a parametric part with
  sliders rather than a frozen mesh. Then remesh(mode="voxel") for even sculpt
  topology, set_mode("sculpt") and sculpt_brush, and the detail is theirs.
- A multi-part result is a COMPONENT TREE: one collection named after the
  project, the core object named after the project, each proposal named
  <project>-<component>. Present core-vs-proposals every time, name the core's
  sliders, and say the keep/scrap line — scrapping a proposal is delete_object
  and nothing else depends on it. merge_for_print then fuses the survivors into
  one watertight shell for the slicer (voxel = nozzle/2 by default; originals
  hidden, not deleted), and check_model is the call that always follows it.
- A project can also own a SCENE: save_project_blend writes the Blender session
  into projects/<name>/<name>.blend as a COPY — the artist's own file is never
  retargeted — which is the only home a sculpt, a lighting setup or placed
  references have. Offer it after real scene work, never after a parametric
  rebuild the script reproduces anyway. open_project_blend loads one back, and
  it is never called unasked: when there is unsaved work it opens nothing and
  answers needs_confirmation, which is a question to put to the artist and wait
  on — offer the save first, and only pass confirm=true after a plain yes.
- The artist's WORKSPACE is yours to drive, not to describe. set_view,
  frame_object, local_view, set_shading, set_overlays, set_mode and sculpt_brush
  run inside their live Blender: "turn the grid on for x, y and z" is
  set_overlays(grid=True, axes=["x","y","z"]), one call, never a list of steps.
  Every one of them returns what changed AND where the switch lives — pass both
  on in ONE line so they learn while you work. Brush technique stays theirs;
  brush selection and settings are yours.
- check_my_work and mesh_diagnose are the buddy tools. check_my_work gathers a
  screenshot of what they are looking at, a clean render and the mesh numbers in
  one call; Read the pictures before commenting. mesh_diagnose locates clipping
  (self-intersections), unsealed edges and density hotspots in millimetres, so a
  critique can say WHERE. Name at most three things, each with its fix.
- MAKER MODE is for anything that has to DO something when you press it. Pick
  the real part FIRST and model to its dimensions — maker_components is the
  catalog of 18 buyable switches, cells, LEDs, screws and magnets with datasheet
  numbers and a purchase note each; a cavity invented from nothing fits nothing.
  circuit_plan answers the resistor question in one of three verdicts ("no
  resistor needed" is a real answer, not an oversight), plunger_plan gives the
  press its travel, its end stop and what returns it, and wiring_guide is the
  handover: 13 soldering steps plus a shopping list, with polarity and
  test-before-glue intact. Geometry is still a PARAMS script composing maker_lib
  (docs/part-authoring.md section 7) and a maker housing is two pieces by
  necessity. End every functional build with wiring_guide.
- FLOOR PLANS are the parametric lane at room scale, and the PLAN FILE IS THE
  MODEL: projects/<slug>/design/floorplan.json carries the whole meaning of the
  drawing, and every id in it is forever because an id names the Blender object.
  The order is fixed. If they gave you a DRAWING, floorplan_extract reads it —
  never your eyes: hand-authoring coordinates off a picture built the wrong
  rooms and a diagonal wall that was in no drawing. Otherwise floorplan_validate
  (schema, resolved numbers, appliance matches quoted so they can be corrected
  in one word). Then a hand-authored design/floorplan.svg echoing your READING
  back (rooms coloured, door swing arcs, labelled fixture rectangles at the
  sizes the lookup resolved), then a MANDATORY approval gate, then
  floorplan_build. After that every edit goes
  through floorplan_diff FIRST and the reply quotes the touched ids ("only
  wall-03 rebuilds") — never a full regen, and never mode="rebuild" unasked.
  A placeholder box is a component SLOT: promoting it is one-way, and an object
  they hand-edited is kept with a warning rather than clobbered.
- If a tool reports a backend is down, say which one and how to start it rather
  than retrying blindly.
"""

#: Cut modes accepted by the segmenting tools. Dict is first so a `mode` object
#: copied straight out of partforge_check survives validation unchanged.
SegmentMode = Union[Dict[str, Any], int, List[float], str]

# mcp >= 2.0 renamed FastMCP to MCPServer; the decorator/run surface is the same.
app = MCPServer("forge", instructions=INSTRUCTIONS, version=__version__)

_OBJECT_DOC = "Object name. Omit to use Blender's active object."


def _target(object_name: Optional[str]) -> Dict[str, Any]:
    """Params dict carrying the optional object target (omitted = active object)."""
    name = (object_name or "").strip()
    return {"object": name} if name else {}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.tool()
def forge_status() -> str:
    """Check whether the Forge backends are up, in one call.

    Reports the Blender add-on socket (127.0.0.1:9876), the Build123d geometry
    service (127.0.0.1:8765) and the meshgen image-to-3D service
    (127.0.0.1:8902) with their versions when reachable. Never fails — use it to
    diagnose any "backend is not running" error, or before starting a session.

    meshgen being down is not a fault: it is optional (an 18.5 GB model
    download) and everything else works without it. The other two are not.
    """
    lines = ["Forge backend status", ""]

    try:
        pong = blender_client.send_command("ping", read_timeout=10.0)
    except ForgeError as exc:
        lines.append(f"Blender add-on   [DOWN] {config.blender_address()}")
        lines.append(f"                 {exc}")
    else:
        version = pong.get("blender_version", "unknown")
        lines.append(
            f"Blender add-on   [UP]   {config.blender_address()} — Blender {version}"
        )

    reachable, detail = service_client.is_available()
    if reachable:
        lines.append(f"Geometry service [UP]   {config.service_address()} — {detail}")
    else:
        lines.append(f"Geometry service [DOWN] {config.service_address()}")
        lines.append(f"                 {detail}")

    reachable, detail = meshgen_client.is_available()
    if reachable:
        lines.append(f"Picture to 3D    [UP]   {config.meshgen_address()} — {detail}")
    else:
        lines.append(f"Picture to 3D    [DOWN] {config.meshgen_address()} — {detail}")
        lines.append(
            "                 optional: image-to-3D only. meshgen_status says what "
            "it would need; parametric parts do not use it."
        )

    return "\n".join(lines)


@app.tool()
def blender_ping() -> str:
    """Ping the Blender add-on and report its Blender version.

    The cheapest way to confirm Blender is running with the Forge server started.
    """
    result = blender_client.send_command("ping", read_timeout=10.0)
    version = result.get("blender_version", "unknown")
    return ok(f"Blender {version} responding on {config.blender_address()}")


# ---------------------------------------------------------------------------
# Blender scene inspection / scripting
# ---------------------------------------------------------------------------


@app.tool()
def get_scene_info() -> str:
    """List every object in the Blender scene: name, type, location, dimensions,
    vertex count and modifier stack, plus which object is active.

    Call this before any operation that targets an object by name, and after
    operations that create or rename objects (separate_loose, boolean, generate).
    """
    return fmt_scene_info(blender_client.send_command("get_scene_info"))


@app.tool()
def execute_blender_python(code: str) -> str:
    """Run arbitrary Python inside Blender (bpy is available) and return its stdout.

    Escape hatch for things the dedicated tools do not cover — prefer the named
    tools (symmetrize, remesh, boolean, ...) when one fits, because they handle
    context and mode switching correctly.

    The code runs on Blender's main thread and can modify the scene irreversibly.
    Use `print()` to return values; the repr of the final expression is also
    reported when there is one.
    """
    result = blender_client.send_command("execute_python", {"code": code})
    output = result.get("output") or ""
    value = result.get("result")
    parts = []
    if output.strip():
        parts.append(output.rstrip())
    if value not in (None, "None", ""):
        parts.append(f"result: {value}")
    return "\n".join(parts) if parts else "(executed; no output)"


# ---------------------------------------------------------------------------
# Common mesh operations
# ---------------------------------------------------------------------------


@app.tool()
def symmetrize(
    direction: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = "+X",
    object: Optional[str] = None,
) -> str:
    """Make a mesh perfectly symmetrical by mirroring one half onto the other.

    `direction` names the side that SURVIVES and is copied across: "+X" keeps
    everything on the positive-X side and mirrors it over to -X, discarding the
    old -X geometry. Use "-X" if the good half is on the negative side.

    Destructive and immediate (no modifier is left behind). Mirroring happens
    about the object's origin, so set_origin may matter first.
    """
    params = _target(object)
    params["direction"] = direction
    blender_client.send_command("symmetrize", params)
    return ok(f"symmetrized {object or 'the active object'} keeping the {direction} side")


@app.tool()
def mirror(
    axis: Literal["X", "Y", "Z"] = "X",
    use_clip: bool = True,
    apply: bool = False,
    object: Optional[str] = None,
) -> str:
    """Add a Mirror modifier across an axis (optionally applying it immediately).

    Unlike symmetrize, this DUPLICATES the mesh across the axis rather than
    trimming it: model half a shape, mirror it, get the whole. Mirroring is about
    the object's origin.

    - use_clip: stop vertices from crossing the mirror plane while editing.
    - apply: True bakes the modifier into the mesh now; False leaves it live and
      editable in the modifier stack (default).
    """
    params = _target(object)
    params.update({"axis": axis, "use_clip": bool(use_clip), "apply": bool(apply)})
    blender_client.send_command("mirror", params)
    verb = "added and applied" if apply else "added"
    return ok(f"Mirror modifier {verb} on {axis} for {object or 'the active object'}")


@app.tool()
def remesh(
    mode: Literal["voxel", "quad"] = "voxel",
    voxel_size: float = 0.01,
    target_faces: int = 5000,
    object: Optional[str] = None,
) -> str:
    """Rebuild a mesh's topology, either as a uniform voxel mesh or as clean quads.

    - mode "voxel": watertight, uniform, triangle-ish. Good for cleaning up a
      sculpt, sealing holes, or preparing for boolean/printing. `voxel_size` is
      in Blender units (metres) — smaller means finer and much heavier; 0.01 is
      a 1 cm voxel. Ignores `target_faces`.
    - mode "quad": Quadriflow retopology to roughly `target_faces` quads. Good
      for game meshes and subdivision. Slow on dense meshes. Ignores `voxel_size`.

    Destructive: the old topology is gone. Returns the new vertex/face counts.
    """
    params = _target(object)
    params["mode"] = mode
    if mode == "voxel":
        params["voxel_size"] = float(voxel_size)
    else:
        params["target_faces"] = int(target_faces)
    result = blender_client.send_command("remesh", params)
    return ok(
        f"remeshed ({mode}) {object or 'the active object'}",
        f"{result.get('vertex_count', '?')} verts, {result.get('face_count', '?')} faces",
    )


@app.tool()
def decimate(ratio: float, object: Optional[str] = None) -> str:
    """Reduce face count with a Decimate (collapse) pass.

    `ratio` is the fraction of faces to KEEP: 0.5 halves the mesh, 0.1 leaves a
    tenth. Values must be in (0, 1]. Use for LODs or to tame a heavy sculpt;
    prefer remesh(mode="quad") when you need clean topology rather than just
    fewer faces.
    """
    if not 0 < ratio <= 1:
        raise ForgeError(f"decimate ratio must be in (0, 1]; got {ratio}.")
    params = _target(object)
    params["ratio"] = float(ratio)
    result = blender_client.send_command("decimate", params)
    return ok(
        f"decimated {object or 'the active object'} to ratio {fmt_number(ratio)}",
        f"{result.get('face_count', '?')} faces",
    )


@app.tool()
def shade(
    mode: Literal["smooth", "flat", "auto"] = "smooth",
    angle: float = 30.0,
    object: Optional[str] = None,
) -> str:
    """Set shading: smooth, flat (faceted), or auto-smooth by angle.

    - "auto" smooths faces that meet at less than `angle` degrees and keeps
      sharper edges crisp — the right default for hard-surface and printed parts
      (30 is a good starting angle).
    - `angle` is ignored for "smooth" and "flat".

    Purely a display/normals change; geometry is untouched.
    """
    params = _target(object)
    params["mode"] = mode
    if mode == "auto":
        params["angle"] = float(angle)
    blender_client.send_command("shade", params)
    suffix = f" at {fmt_number(angle)}°" if mode == "auto" else ""
    return ok(f"shading set to {mode}{suffix} on {object or 'the active object'}")


@app.tool()
def apply_transforms(
    location: bool = True,
    rotation: bool = True,
    scale: bool = True,
    object: Optional[str] = None,
) -> str:
    """Bake an object's transform into its mesh data (Object > Apply).

    After this the chosen channels reset to identity (scale 1, rotation 0, and/or
    origin at the world origin) while the object looks unchanged. Do this before
    exporting, before booleans, and before modifiers that care about real scale —
    a non-uniform scale silently distorts most of them.

    All three channels default to True; pass False to leave one alone.
    """
    if not (location or rotation or scale):
        raise ForgeError("Nothing to apply: location, rotation and scale are all False.")
    params = _target(object)
    params.update(
        {"location": bool(location), "rotation": bool(rotation), "scale": bool(scale)}
    )
    blender_client.send_command("apply_transforms", params)
    applied = ", ".join(
        n for n, on in (("location", location), ("rotation", rotation), ("scale", scale)) if on
    )
    return ok(f"applied {applied} on {object or 'the active object'}")


@app.tool()
def set_origin(
    type: Literal["geometry", "bottom", "cursor"] = "geometry",
    object: Optional[str] = None,
) -> str:
    """Move an object's origin point without moving the object.

    - "geometry": origin to the centre of the mesh's bounds.
    - "bottom": origin to the centre of the mesh's lowest face — what you want
      for a printed part that should sit on Z=0, and for sane rotation.
    - "cursor": origin to the current 3D cursor position.

    The origin is the pivot for rotation, scaling, mirroring and symmetrize.
    """
    params = _target(object)
    params["type"] = type
    blender_client.send_command("set_origin", params)
    return ok(f"origin set to {type} on {object or 'the active object'}")


@app.tool()
def boolean(
    operand: str,
    operation: Literal["UNION", "DIFFERENCE", "INTERSECT"] = "DIFFERENCE",
    apply: bool = True,
    delete_operand: bool = True,
    object: Optional[str] = None,
) -> str:
    """Combine two objects with a boolean: cut, fuse, or intersect.

    - DIFFERENCE: subtract `operand` from the target (drill a hole, cut a slot).
    - UNION: fuse them into one solid.
    - INTERSECT: keep only the overlapping volume.

    `operand` is the name of the cutter/second object; `object` is the target
    that gets modified. Booleans want clean, manifold, non-coplanar meshes —
    if the result looks wrong, remesh the inputs or nudge the cutter slightly.

    apply=True (default) bakes the result immediately; delete_operand=True
    removes the cutter afterwards.
    """
    if not operand or not operand.strip():
        raise ForgeError("boolean requires the name of the operand (cutter) object.")
    params = _target(object)
    params.update(
        {
            "operand": operand.strip(),
            "operation": operation,
            "apply": bool(apply),
            "delete_operand": bool(delete_operand),
        }
    )
    blender_client.send_command("boolean", params)
    return ok(
        f"{operation} of '{operand}' into {object or 'the active object'}",
        ("applied" if apply else "left as a live modifier")
        + (", operand deleted" if delete_operand else ""),
    )


@app.tool()
def merge_by_distance(distance: float = 0.0001, object: Optional[str] = None) -> str:
    """Weld vertices that sit closer together than `distance` (Blender units/metres).

    The standard cleanup for split seams, duplicate verts and non-manifold edges
    left by imports, mirroring or separated pieces. 0.0001 (0.1 mm at scene
    scale) is a safe default; raise it carefully — too large collapses detail.

    Returns how many vertices were removed.
    """
    params = _target(object)
    params["distance"] = float(distance)
    result = blender_client.send_command("merge_by_distance", params)
    return ok(
        f"merged verts within {fmt_number(distance, 6)} on {object or 'the active object'}",
        f"{result.get('removed', '?')} vertices removed",
    )


@app.tool()
def separate_loose(object: Optional[str] = None) -> str:
    """Split a mesh into one object per disconnected shell.

    Use when a single object secretly contains several pieces (after a boolean,
    an import, or a multi-part generate) and you need to name, move or export
    them separately. Returns the names of the resulting objects.
    """
    result = blender_client.send_command("separate_loose", _target(object))
    names = result.get("objects") or []
    listed = ", ".join(str(n) for n in names) if names else "(none reported)"
    return ok(f"separated {object or 'the active object'} into {len(names)} object(s)", listed)


# ---------------------------------------------------------------------------
# Object management
# ---------------------------------------------------------------------------


@app.tool()
def select_object(name: str) -> str:
    """Select an object and make it active, so later tools can omit `object`."""
    if not name or not name.strip():
        raise ForgeError("select_object requires an object name.")
    blender_client.send_command("select_object", {"name": name.strip()})
    return ok(f"'{name}' selected and made active")


@app.tool()
def rename_object(name: str, new_name: str) -> str:
    """Rename an object. Blender may append .001 if the name is taken; the final
    name is returned."""
    if not name.strip() or not new_name.strip():
        raise ForgeError("rename_object requires both `name` and `new_name`.")
    result = blender_client.send_command(
        "rename_object", {"name": name.strip(), "new_name": new_name.strip()}
    )
    final = result.get("name", new_name)
    return ok(f"'{name}' renamed to '{final}'")


@app.tool()
def delete_object(name: str) -> str:
    """Delete an object from the scene. Not undoable from here — confirm with the
    user before removing anything they may still want."""
    if not name or not name.strip():
        raise ForgeError("delete_object requires an object name.")
    blender_client.send_command("delete_object", {"name": name.strip()})
    return ok(f"'{name}' deleted")


@app.tool()
def export_stl(path: str, objects: Optional[List[str]] = None) -> str:
    """Export objects from Blender to a single STL file.

    `path` is where the .stl is written (absolute is safest; relative paths are
    resolved against the server's working directory, and missing folders are
    created). `objects` is a list of object names; omit it to export the current
    selection/active object.

    STL carries triangles only — no units metadata, no colour. For a dimensioned
    or solid-model export of a PartForge part, use partforge_export (STEP/3MF).
    """
    out = resolve_path(path, label="export path")
    if out.suffix.lower() != ".stl":
        out = out.with_suffix(".stl")
    ensure_parent_dir(out)
    names = [n.strip() for n in (objects or []) if n and n.strip()]
    result = blender_client.send_command(
        "export_stl", {"objects": names, "path": str(out)}
    )
    written = result.get("path", str(out))
    subject = ", ".join(names) if names else "the current selection"
    return ok(f"exported {subject} to {written}")


# ---------------------------------------------------------------------------
# Reference images (Phase 6c)
# ---------------------------------------------------------------------------


@app.tool()
def load_reference(
    path: str,
    view: Literal["front", "side", "top"] = "front",
    size_mm: Optional[float] = None,
    name: Optional[str] = None,
) -> str:
    """Put the artist's sketch or photo in the Blender viewport to model against.

    Offer this whenever they attached a reference image: it lands as a
    half-transparent image plane just behind the origin, facing the named
    orthographic view (front = Numpad 1, side = Numpad 3, top = Numpad 7), so
    they can eyeball the model against the picture instead of taking your word
    for the proportions.

    `size_mm` is the picture's LONGER side in millimetres (default 200); the
    shorter side follows the file's own pixel aspect, so nothing is stretched.
    `name` defaults to `Ref-<view>`, and loading the same name again REPLACES
    that reference rather than stacking another copy on it.

    It is an empty, not geometry: it can never be exported, printed or
    accidentally booleaned, and the artist can move, scale or hide it like any
    other object. `.png`, `.jpg`, `.jpeg`, `.webp` and `.bmp` only.

    This is for comparing against — never trace it. Geometry is always built
    from parameters.
    """
    image = reference_path(path)
    params: Dict[str, Any] = {"path": str(image), "view": view}
    if size_mm is not None:
        if not isinstance(size_mm, (int, float)) or isinstance(size_mm, bool):
            raise ForgeError("size_mm must be a number of millimetres.")
        if size_mm <= 0:
            raise ForgeError(
                f"size_mm must be a positive number of millimetres (got {size_mm})."
            )
        params["size_mm"] = float(size_mm)
    if name is not None and name.strip():
        params["name"] = name.strip()

    result = blender_client.send_command("load_reference", params)
    return fmt_reference_report(image, view, result)


# ---------------------------------------------------------------------------
# Previews — the assistant's eyes
# ---------------------------------------------------------------------------


@app.tool()
def render_preview(
    objects: Optional[List[str]] = None,
    view: Literal["iso", "front", "side", "top"] = "iso",
    resolution: Optional[int] = None,
    shading: Literal["solid", "material"] = "solid",
) -> str:
    """Your eyes — look at what you made.

    Renders the Blender scene to a PNG and hands you the path.
    **Then Read that file.** Rendering without looking is the same blind
    design with an extra tool call in front of it.

    Call this after generating or meaningfully changing anything visual, and
    ALWAYS when the artist gave you a reference picture. Print checks answer
    "can this be printed"; they say nothing about whether it looks like the
    thing. A part can pass every check and still be stiff, sparse and flat next
    to the picture it came from — that is exactly the failure this tool exists
    to catch, and you cannot catch it from parameter values.

    - `objects`: names to frame; omit for every visible mesh. Anything else is
      hidden for the render, so one part is one part.
    - `view`: `iso` (default, a 3/4 orbit — best for silhouette and form),
      or `front` / `side` / `top`, which are the SAME three projections
      `load_reference` uses. Render `front` when you have a front reference and
      the two line up 1:1.
    - `resolution`: square pixels, 128-2048, default 768.
    - `shading`: `solid` (default — clay, fast, no GPU needed, form over
      colour) or `material` for the object's own materials.

    Nothing in the scene changes: the camera, the render settings and the
    artist's viewport are all put back, and it costs no undo step.

    When you look, compare against the reference and name what you see:
    density (too few leaves? too sparse a band?), proportions, silhouette,
    softness. If it visibly misses, change the parameters or the script and
    render again — then show the artist and say what still differs.
    """
    names = normalize_preview_objects(objects)
    pixels = normalize_preview_resolution(resolution)
    out = preview_path(view, names)

    params: Dict[str, Any] = {
        "path": str(out),
        "view": view,
        "resolution": pixels,
        "shading": shading,
    }
    if names:
        params["objects"] = names

    result = blender_client.send_command(
        "render_preview", params, read_timeout=config.PREVIEW_TIMEOUT
    )
    result.setdefault("path", str(out))
    return fmt_preview_report(result)


# ---------------------------------------------------------------------------
# The workspace copilot (Phase 8) — you drive their Blender, you don't describe it
# ---------------------------------------------------------------------------


@app.tool()
def set_view(
    view: Literal["front", "back", "left", "right", "side", "top", "bottom",
                  "iso", "camera"] = "front",
    ortho: Optional[bool] = None,
) -> str:
    """Turn the artist's viewport to a named angle. DO IT — never describe it.

    You are running inside their Blender. "Show me the front", "let me see it
    from the side", "get me back to a 3/4 view" are one call, not a tutorial.

    `front`/`back`/`left`/`right`/`top`/`bottom` are flat (orthographic) by
    default — the projection you can measure against a reference, and the same
    three `render_preview` and `load_reference` use. `iso` is the 3/4 orbit and
    stays in perspective. `camera` looks through the scene camera.

    Applies to every open 3D viewport. Reply with what changed plus the one
    line saying where the switch lives — the numpad — so they can do it
    themselves next time.
    """
    params: Dict[str, Any] = {"view": view}
    if ortho is not None:
        params["ortho"] = bool(ortho)
    return fmt_workspace_report(blender_client.send_command("set_view", params))


@app.tool()
def frame_object(
    object: Optional[str] = None,
    all: bool = False,
    margin: Optional[float] = None,
) -> str:
    """Zoom their viewport onto one object (or everything visible). DO IT.

    "I can't see it", "where did it go", "zoom in on the head" — this, not a
    paragraph about Numpad dot. Nothing is selected or deselected: their
    selection stays theirs.

    - `object`: what to frame; omit for the active object.
    - `all`: frame every visible mesh instead.
    - `margin`: breathing room, 1.0-4.0 (default 1.25).
    """
    params = _target(object)
    if all:
        params["all"] = True
    if margin is not None:
        params["margin"] = float(margin)
    return fmt_workspace_report(blender_client.send_command("frame_object", params))


@app.tool()
def local_view(enable: Optional[bool] = None, object: Optional[str] = None) -> str:
    """Isolate one object so everything else stops getting in the way. DO IT.

    Local View hides everything except the selection, which is the right answer
    to "I can't work on the ear with the body in the way".

    - `enable`: true to go in, false to come out, omit to toggle.
    - `object`: isolate this one — it is selected and made active first,
      because Local View isolates the SELECTION and there is no other way to
      say which object was meant.
    """
    params = _target(object)
    if enable is not None:
        params["enable"] = bool(enable)
    return fmt_workspace_report(blender_client.send_command("local_view", params))


@app.tool()
def set_shading(
    mode: Literal["solid", "wireframe", "material", "rendered"] = "solid",
) -> str:
    """Switch how their viewport draws the model. DO IT.

    `solid` (grey clay, fast, shows form), `wireframe` (see-through, edges
    only — what you want for checking topology), `material` (the object's own
    colours), `rendered` (full lighting, slowest).

    Applies to every open 3D viewport.
    """
    return fmt_workspace_report(
        blender_client.send_command("set_shading", {"mode": mode})
    )


@app.tool()
def set_overlays(
    grid: Optional[bool] = None,
    axes: Optional[Union[List[str], bool, str]] = None,
    wireframe: Optional[bool] = None,
    stats: Optional[bool] = None,
    overlays: Optional[bool] = None,
    origins: Optional[bool] = None,
    cursor: Optional[bool] = None,
    text: Optional[bool] = None,
    face_orientation: Optional[bool] = None,
    xray: Optional[bool] = None,
) -> str:
    """Turn viewport overlays on and off — the grid, the X/Y/Z axis lines, more.

    **This is the answer to "I want to enable grid view for x, y, z axis".**
    One call: `set_overlays(grid=True, axes=["x", "y", "z"])`. Not nine steps.
    You are inside their Blender and every one of those steps is a property you
    can set. Do it, then say in one line that it is on and that the Overlays
    dropdown is where they toggle it themselves.

    - `grid`: the floor grid (and the orthographic grid, which is the same idea
      seen from the front/side/top).
    - `axes`: which coloured axis lines show — `["x","y","z"]`, `"all"`, `true`
      for all three, or `[]` for none. The list is authoritative: an axis not
      in it is turned OFF.
    - `wireframe`: draw the edges over the surface.
    - `stats`: the vertex/face counter.
    - `overlays`: the master switch for all of them.
    - `origins`, `cursor`, `text`, `face_orientation` (blue outside / red
      inside — the fast way to spot flipped normals), `xray` (see-through).

    Anything you do not name is left exactly as the artist had it. Applies to
    every open 3D viewport.
    """
    params: Dict[str, Any] = {}
    for key, value in (
        ("grid", grid), ("wireframe", wireframe), ("stats", stats),
        ("overlays", overlays), ("origins", origins), ("cursor", cursor),
        ("text", text), ("face_orientation", face_orientation), ("xray", xray),
    ):
        if value is not None:
            params[key] = bool(value)
    if axes is not None:
        params["axes"] = normalize_axes(axes)
    if not params:
        raise ForgeError(
            "Name at least one overlay to change: grid, axes, wireframe, stats, "
            "overlays, origins, cursor, text, face_orientation, xray."
        )
    return fmt_workspace_report(blender_client.send_command("set_overlays", params))


@app.tool()
def set_mode(
    mode: Literal["object", "edit", "sculpt", "vertex_paint", "weight_paint",
                  "texture_paint", "pose"] = "object",
    object: Optional[str] = None,
) -> str:
    """Put Blender into a mode. DO IT — do not tell them where the dropdown is.

    "Put me in sculpt mode", "I want to edit the vertices", "let me paint
    weights" are one call. The object is checked first: Sculpt Mode on an
    armature comes back as a sentence, not a Blender error box nobody sees.

    - `object`: which object goes into that mode; omit for the active one. It
      is selected and made active, because Blender's mode belongs to the active
      object and switching without that is the commonest silent no-op.

    Sculpt Mode is usually the *start* of a shape-3 answer, not the whole of
    it: put them in the mode, set the brush with `sculpt_brush`, and THEN walk
    them through the strokes, which are genuinely theirs.
    """
    params = _target(object)
    params["mode"] = mode
    return fmt_workspace_report(blender_client.send_command("set_mode", params))


@app.tool()
def sculpt_brush(
    brush: Optional[str] = None,
    size: Optional[int] = None,
    strength: Optional[float] = None,
    symmetry_x: Optional[bool] = None,
    symmetry_y: Optional[bool] = None,
    symmetry_z: Optional[bool] = None,
    dyntopo: Optional[bool] = None,
    object: Optional[str] = None,
) -> str:
    """Pick their sculpt brush and set it up. DO IT.

    The line to hold: brush **technique** is theirs — nobody can drag their
    stylus for them, and "how do I sculpt a fold" is a shape-3 walkthrough.
    Brush **selection and settings** are yours. So when they say "I want to add
    wrinkles to the cloak", set the Crease brush up for them first, then give
    the strokes.

    - `brush`: the name (`"Clay Strips"`, `"Draw"`, `"Crease Sharp"`,
      `"Smooth"`, `"Grab"`, `"Inflate/Deflate"`). Case- and
      underscore-insensitive, and a miss comes back naming the closest brush
      that exists. Omit it to change only the settings.
    - `size`: brush radius in screen pixels, 1-5000.
    - `strength`: 0.0-10.0.
    - `symmetry_x` / `_y` / `_z`: mirror every stroke. Turn X on for a face.
    - `dyntopo`: dynamic topology — the mesh grows new polygons where they
      sculpt. The right answer to a starved region they want detail in.

    The object is put into Sculpt Mode first if it is not already, because that
    is what makes the brushes exist at all.
    """
    params = _target(object)
    if brush is not None:
        params["brush"] = brush
    if size is not None:
        params["size"] = size
    if strength is not None:
        params["strength"] = strength
    for key, value in (("symmetry_x", symmetry_x), ("symmetry_y", symmetry_y),
                       ("symmetry_z", symmetry_z), ("dyntopo", dyntopo)):
        if value is not None:
            params[key] = bool(value)
    if len(params) == len(_target(object)):
        raise ForgeError(
            "Nothing to change: give a brush name, a size, a strength, a "
            "symmetry axis or dyntopo."
        )
    result = blender_client.send_command("sculpt_brush", params)
    extra = []
    available = result.get("available") or []
    if available:
        extra.append("other brushes: " + ", ".join(str(n) for n in available[:12])
                     + (", ..." if len(available) > 12 else ""))
    return fmt_workspace_report(result, extra)


# ---------------------------------------------------------------------------
# Buddy mode — a teacher's eyes on the work in progress
# ---------------------------------------------------------------------------


@app.tool()
def mesh_diagnose(
    object: Optional[str] = None,
    examples: Optional[int] = None,
    density_ratio: Optional[float] = None,
) -> str:
    """Measure what is wrong with a mesh, and WHERE, in millimetres.

    This is the numbers behind a critique. A render shows you the form; this
    finds what a render cannot: the surface passing through itself (the
    artist's word for it is **clipping**), edges that are not sealed, faces
    with no area, regions with far too few or far too many polygons ("we need
    to remesh here"), ngons, stray geometry, and a scale that is a units
    mistake rather than a decision.

    Every defect comes back with a location in millimetres. Use them. "There is
    some self-intersection" is worth nothing to a sculptor; "the left ear
    passes through the head around (-42, 18, 96) mm" is somewhere to put the
    mouse.

    - `object`: which mesh; omit for the active object.
    - `examples`: located examples per problem, 1-25 (default 5).
    - `density_ratio`: how far off the median a region must be before it
      counts, default 4.

    Read-only and fast (under a second on a 200 000-face sculpt), so it is safe
    to run whenever you are about to comment on someone's model. Name at most
    THREE things back — a list of nine defects is not a critique.
    """
    params = _target(object)
    if examples is not None:
        if isinstance(examples, bool) or not isinstance(examples, int):
            raise ForgeError("examples must be a whole number between 1 and 25.")
        if not 1 <= examples <= 25:
            raise ForgeError(
                f"examples must be between 1 and 25 (got {examples})."
            )
        params["examples"] = examples
    if density_ratio is not None:
        if isinstance(density_ratio, bool) or not isinstance(density_ratio, (int, float)):
            raise ForgeError("density_ratio must be a number.")
        if not 1.5 <= float(density_ratio) <= 100.0:
            raise ForgeError(
                f"density_ratio must be between 1.5 and 100 (got {density_ratio})."
            )
        params["density_ratio"] = float(density_ratio)
    return fmt_diagnose_report(
        blender_client.send_command("mesh_diagnose", params)
    )


@app.tool()
def check_my_work(object: Optional[str] = None, resolution: Optional[int] = None) -> str:
    """Look over the artist's shoulder: their view, a clean render, and the numbers.

    One call gathers everything a teacher needs and hands you the paths:

    1. `capture_viewport` — **what they are actually looking at**: their angle,
       their shading, their overlays, the mask they have painted.
    2. `render_preview` (3/4 view) — the same model lit cleanly, so the form and
       the silhouette read.
    3. `mesh_diagnose` — clipping, unsealed edges, density hotspots and starved
       regions, each with a place in millimetres.

    **Then Read both pictures before you say a word.** A critique written from
    the numbers alone is half a critique, and one written from neither is a
    guess dressed up as teaching.

    Answer as a teacher, not a report: one clause on what is working, then at
    most **three** concrete things, each with where it is and what fixes it —
    offer to do the ones a tool can do, give numbered steps for the ones only
    their hand can. If it is genuinely clean, say so in a line and let them get
    back to work. Never repeat a note you already gave them.
    """
    images: List[tuple] = []
    notes: List[str] = []

    viewport = util.checkin_path("viewport")
    params: Dict[str, Any] = {"path": str(viewport)}
    if resolution is not None:
        params["resolution"] = resolution
    try:
        result = blender_client.send_command(
            "capture_viewport", params, read_timeout=config.PREVIEW_TIMEOUT
        )
        images.append(("what they are looking at right now",
                       result.get("path") or str(viewport)))
    except BackendUnavailable:
        raise
    except ForgeError as exc:
        notes.append(f"no viewport screenshot ({exc})")

    preview = util.checkin_path("render")
    params = {"path": str(preview), "view": "iso", "resolution": 768,
              "shading": "solid"}
    name = (object or "").strip()
    if name:
        params["objects"] = [name]
    try:
        result = blender_client.send_command(
            "render_preview", params, read_timeout=config.PREVIEW_TIMEOUT
        )
        images.append(("a clean 3/4 render of the same model",
                       result.get("path") or str(preview)))
    except BackendUnavailable:
        raise
    except ForgeError as exc:
        notes.append(f"no clean render ({exc})")

    diagnosis = ""
    try:
        diagnosis = fmt_diagnose_report(
            blender_client.send_command("mesh_diagnose", _target(object))
        )
    except BackendUnavailable:
        raise
    except ForgeError as exc:
        notes.append(f"no mesh check ({exc})")

    scene = ""
    try:
        scene = fmt_scene_info(blender_client.send_command("get_scene_info"))
    except BackendUnavailable:
        raise
    except ForgeError as exc:
        notes.append(f"no scene listing ({exc})")

    return fmt_check_in_report(images, diagnosis, scene, notes)


# ---------------------------------------------------------------------------
# The geometric gate — renders judge beauty, this judges truth
# ---------------------------------------------------------------------------


@app.tool()
def verify_design(
    object: Optional[str] = None,
    reference_image: Optional[str] = None,
    profile: Literal["game", "print", "any"] = "any",
    poly_budget: Optional[int] = None,
    symmetry_axis: Literal["X", "Y", "Z"] = "X",
    examples: Optional[int] = None,
) -> str:
    """The other half of looking: is this mesh TRUE, whatever it looks like.

    `render_preview` judges beauty. This judges truth, and you need both,
    because judging by eye alone is measurably biased toward prettiness: the
    same model scores 78 ELO higher presented as a splat than as a mesh, and
    about a quarter of paired visual judgements reverse when the two candidates
    swap places. A render loop on its own will hand over a beautiful mesh nobody
    can use and you will believe you succeeded.

    One scored report, one call:

    - **defects** — everything `mesh_diagnose` finds (clipping, unsealed edges,
      density, scale), composed in rather than repeated.
    - **poly budget** — the face count against a target, so "game-ready" is a
      number.
    - **UVs** — islands, flipped faces, texture-density distortion, an overlap
      estimate. Skipped when there are none, which is itself the finding.
    - **symmetry residual** — the distance from the mesh to its own mirror, in
      millimetres. **Reported, never judged**: a swept tail and a failed
      symmetrize produce the same number and only the artist knows which it is.
    - **edge loops at joints** — how many loops cross each deformation zone,
      read off an armature or off the RigForge tag boundaries. Under three and
      the bend creases.
    - **silhouette IoU** — with `reference_image`, how much of the front
      silhouette actually overlaps the picture they gave you. This is the only
      measurement that answers "is it the SHAPE of the thing they asked for".

    - `profile`: what the mesh is FOR. `game` gates loops, UVs and the budget;
      `print` gates defects and silhouette only — **bed fit, wall thickness and
      overhangs belong to `partforge_check` / `check_model`**, and this
      deliberately does not duplicate them; `any` (default) gates the universal
      axes.
    - `poly_budget`: faces the target platform allows (default 15 000 for
      `game`, ungated otherwise).
    - `reference_image`: the artist's picture. A plain background makes the
      measurement reliable and the report says when it did not have one.

    **Every claim carries a tier.** `measured` is a computed number with a
    definition; `heuristic` is a number that took a judgement call. Quote them
    apart — a silhouette thresholded off a photograph is not the same kind of
    fact as a face count, and saying so is the difference between a report and
    a guess with numbers in it.

    Read-only, and it costs no undo step.
    """
    # The socket protocol's field really is called `for` — it is the natural
    # word and JSON does not care. Python does, so the tool's argument is
    # `profile` and the rename happens here, in one place.
    params = _target(object)
    params["for"] = profile
    params["symmetry_axis"] = symmetry_axis

    if examples is not None:
        if isinstance(examples, bool) or not isinstance(examples, int):
            raise ForgeError("examples must be a whole number between 1 and 25.")
        if not 1 <= examples <= 25:
            raise ForgeError(f"examples must be between 1 and 25 (got {examples}).")
        params["examples"] = examples

    budget = normalize_poly_budget(poly_budget)
    if budget is not None:
        params["poly_budget"] = budget

    if reference_image is not None and str(reference_image).strip():
        params["reference_image"] = str(reference_path(reference_image))

    return fmt_verify_report(
        blender_client.send_command(
            "verify_design", params, read_timeout=config.PREVIEW_TIMEOUT
        )
    )


@app.tool()
def turntable(
    objects: Optional[List[str]] = None,
    views: Optional[int] = None,
    resolution: Optional[int] = None,
    elevation: Optional[float] = None,
) -> str:
    """Every side of the model on ONE picture. Use it for anything organic.

    Renders N views around Z and stitches them into a single contact sheet, then
    hands you the path. **Then Read that file.**

    A single hero render is the cheapest way to be wrong about a mesh. It hides
    interpenetration, the flat side nobody modelled, and the top of the head —
    which is exactly the set of things a generated mesh gets wrong. This is the
    judging rig that was actually measured to work: a fixed turntable, every
    tile framed identically, so anything that changes between tiles is the model
    changing and never the camera.

    Call it instead of `render_preview` for anything organic or generated, after
    `generate_3d`, after a retopo, and before saying a creature is done.

    - `objects`: names to spin; omit for every visible mesh.
    - `views`: 4-64, default **24** — the protocol number. Fewer hides the
      sides between the ones you kept.
    - `resolution`: pixels per tile, 64-512, default **256**, also the protocol
      number. A bigger tile buys nothing when there are 24 on one sheet.
    - `elevation`: degrees above the equator, -80 to 80, default 15. A dead
      level orbit hides the top of everything.

    **If you compare two turntables, read them in BOTH orders.** About a quarter
    of paired visual judgements reverse when the presentation order swaps, so a
    one-way read is a coin flip wearing a verdict's clothes. If your answer
    flips, say it is too close to call and decide on `verify_design`'s numbers.

    Read-only: the render settings and the camera are borrowed and put back.
    """
    names = normalize_preview_objects(objects)
    count = normalize_turntable_views(views)
    pixels = normalize_turntable_resolution(resolution)
    out = turntable_path(count, names)

    params: Dict[str, Any] = {
        "path": str(out),
        "views": count,
        "resolution": pixels,
    }
    if names:
        params["objects"] = names
    if elevation is not None:
        if isinstance(elevation, bool) or not isinstance(elevation, (int, float)):
            raise ForgeError("elevation must be a number of degrees.")
        if not -80.0 <= float(elevation) <= 80.0:
            raise ForgeError(
                f"elevation must be between -80 and 80 degrees (got {elevation})."
            )
        params["elevation"] = float(elevation)

    result = blender_client.send_command(
        "turntable", params, read_timeout=config.PREVIEW_TIMEOUT
    )
    result.setdefault("path", str(out))
    return fmt_turntable_report(result)


# ---------------------------------------------------------------------------
# PartForge (geometry service)
# ---------------------------------------------------------------------------


@app.tool()
def partforge_parse_params(script_path: str) -> str:
    """Read a PartForge script's PARAMS block: names, values, units, ranges, docs.

    No geometry is built, so this is fast and safe. Use it to learn what knobs a
    part has before calling partforge_generate/partforge_export with overrides,
    or to check what a slider in the Blender panel maps to.
    """
    path, source = read_script(script_path)
    payload = service_client.parse_params(source)
    return f"{path}\n\n{fmt_params(payload.get('params'))}"


@app.tool()
def partforge_generate(
    script_path: str,
    overrides: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    collection: Optional[str] = None,
) -> str:
    """Build a PartForge part and load it into Blender — the whole regenerate loop.

    Runs the Build123d script through the geometry service, then pushes the
    tessellated mesh into Blender, replacing that object's mesh data in place so
    the object's transform, panel state and selection survive. This is the single
    call behind "regenerate the part" or "make the wall 3 mm and rebuild".

    - `overrides`: {"param_name": value} against the script's PARAMS block, in
      the parameter's declared unit. Values are validated against min/max by the
      service. Overrides apply to THIS build only — edit the script to make a
      change stick.
    - `name`: Blender object name; defaults to the script's filename (or its
      folder name when the file is generically named, e.g. projects/x/part.py).
    - `collection`: the Blender collection the object lands in (created if it
      does not exist). Use it for a multi-part design: one collection named
      after the project, the core object named after the project, and every
      proposal named `<project>-<component>` (`gecko-bowl-collar`). That naming
      is what makes "scrap the collar" a one-object delete and lets
      merge_for_print take the collection and mean "everything still visible in
      it". A collection is only applied when the object is created — an existing
      object keeps the collection it is already in, like every other thing
      `replace` preserves.

    Reports vertex/face counts, bounding box in mm and watertightness. If Blender
    is not running the part is still built and the stats are still reported — it
    just is not loaded into the viewport.
    """
    path, source = read_script(script_path)
    payload = service_client.generate(source, overrides)

    stats = payload.get("stats") or {}
    mesh = payload.get("mesh") or {}
    vertices = mesh.get("vertices") or []
    faces = mesh.get("faces") or []
    object_name = (name or "").strip() or object_name_for_script(path)

    lines = [
        f"Built {path.name} as '{object_name}'",
        f"  {fmt_stats(stats)}",
        f"  overrides: {fmt_overrides(overrides)}",
    ]

    if not vertices:
        lines.append(
            "  NOT loaded into Blender: the service returned an empty mesh "
            "(check the script's build() return value)."
        )
        return "\n".join(lines)

    load_params: Dict[str, Any] = {
        "name": object_name,
        "vertices": vertices,
        "faces": faces,
        "replace": True,
    }
    if collection and collection.strip():
        load_params["collection"] = collection.strip()

    try:
        loaded = blender_client.send_command("load_mesh", load_params)
    except BackendUnavailable as exc:
        lines.append(f"  NOT loaded into Blender — {exc}")
    except ForgeError as exc:
        lines.append(f"  Blender refused the mesh — {exc}")
    else:
        lines.append(
            f"  Loaded into Blender as '{loaded.get('object', object_name)}' "
            f"({loaded.get('vertex_count', '?')} verts, "
            f"{loaded.get('face_count', '?')} faces), mesh replaced in place."
            + (f" In the collection '{collection.strip()}'."
               if collection and collection.strip() else "")
        )

    params = payload.get("params")
    if params:
        lines.append("  resolved parameters:")
        lines.append(fmt_params(params))
    return "\n".join(lines)


@app.tool()
def partforge_export(
    script_path: str,
    output_path: str,
    format: Literal["stl", "step", "3mf"] = "stl",
    overrides: Optional[Dict[str, Any]] = None,
) -> str:
    """Export a PartForge part to a real CAD/print file from the solid, not the mesh.

    The geometry service rebuilds the part and writes it directly, so the export
    is exact rather than a re-tessellation of whatever is in Blender.

    - "step": true B-rep solid — for Fusion/CAD editing downstream.
    - "3mf": mesh plus units/metadata — the best slicer format.
    - "stl": plain triangles — universal, unitless.

    `overrides` applies the same way as in partforge_generate. Missing output
    folders are created; the extension is corrected to match `format`.
    """
    path, source = read_script(script_path)
    out = resolve_path(output_path, label="output path")
    wanted = f".{format.lower()}"
    if out.suffix.lower() != wanted:
        out = out.with_suffix(wanted)
    ensure_parent_dir(out)

    payload = service_client.export(source, overrides, format.lower(), str(out))
    written = payload.get("path", str(out))
    return ok(
        f"exported {path.name} as {format.upper()} to {written}",
        f"overrides: {fmt_overrides(overrides)}",
    )


# ---------------------------------------------------------------------------
# PartForge authoring — making a part that does not exist yet
# ---------------------------------------------------------------------------


@app.tool()
def partforge_new_part(
    name: str,
    script_source: str,
    overwrite: bool = False,
    components: Optional[List[str]] = None,
) -> str:
    """Create (or revise) a parametric part: write a PARAMS script into projects/.

    This is how a part that does not exist yet comes into being. Read
    `docs/part-authoring.md` FIRST — it is the authoring rulebook (printability
    rules plus the `forge_lib` helper catalog) — then compose the script and pass
    the whole file as `script_source`.

    - `name` is plain words ("small magnet holder"); it becomes the folder
      `projects/small-magnet-holder/` and the file `part.py` inside it. Anything
      that looks like a path is refused — nothing is ever written elsewhere.
    - The script is validated through the geometry service's /parse_params
      BEFORE anything touches disk. A bad PARAMS block or a script that will not
      import fails here, with the service's own message, and no file is created.
    - A minimal `spec.json` is written alongside it (name, description
      placeholder, the parameters mirrored from the parsed schema, and a print
      section pointing at templates/printer.json) unless one already exists.
    - `overwrite=true` is also the REVISION path: same tool, same validation.
      That is how the self-correction loop edits a script after a failed check.
    - `components`: the proposal pieces this design lands as — `["collar",
      "ear-l", "ear-r"]`. It records the component tree in spec.json (the
      collection and the core are the project itself, each proposal
      `<project>-<component>`), so a later session knows which object is the
      dimensioned core and which ones the artist may scrap. Pass it whenever the
      answer is more than one object, and build each piece with
      `partforge_generate(..., name=..., collection=...)` to match.

    Returns the script path and the parsed parameter table, so you can confirm
    what you built and name the useful sliders back to the artist.

    Nothing is built or shown by this call. Follow with partforge_open_in_panel,
    partforge_generate, then partforge_check — always check.
    """
    slug = project_slug(name)
    folder, script, spec = project_paths(slug)
    source = normalize_script_source(script_source)

    if script.exists() and not overwrite:
        raise ForgeError(
            f"{script} already exists. That part is already there — regenerate it "
            f"with partforge_generate('{script}'), or pass overwrite=true to "
            "replace the script with this new version (which is what revising a "
            "part after a failed check looks like). To make a DIFFERENT part, "
            "give it another name."
        )

    # Validated before written: a script that cannot be parsed never lands on
    # disk, so projects/ never fills up with drafts that do not run.
    payload = service_client.parse_params(source)
    params = payload.get("params") or {}

    try:
        folder.mkdir(parents=True, exist_ok=True)
        existed = script.exists()
        script.write_text(source, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ForgeError(f"Could not write {script}: {exc}") from exc

    spec_created = False
    if not spec.exists():
        try:
            spec.write_text(
                json.dumps(spec_document(name, slug, params,
                                         components=components), indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            spec_created = True
        except OSError as exc:  # a missing spec must not lose the script
            return fmt_new_part_report(
                name=name, slug=slug, script=script, params=params,
                created=not existed, spec=None, spec_created=False,
            ) + f"\n  (spec.json could not be written: {exc})"
    elif components:
        # An existing spec is the artist's file, so only the one block this call
        # actually knows about is touched — and a spec that cannot be read is
        # left exactly as it is rather than replaced with a guess.
        note = update_spec_components(spec, slug, components)
        if note:
            return fmt_new_part_report(
                name=name, slug=slug, script=script, params=params,
                created=not existed, spec=spec, spec_created=False,
            ) + f"\n  ({note})"

    return fmt_new_part_report(
        name=name,
        slug=slug,
        script=script,
        params=params,
        created=not existed,
        spec=spec,
        spec_created=spec_created,
    )


@app.tool()
def save_design_doc(project: str, filename: str, content: str) -> str:
    """Save one design document — requirements, a concept diagram, a components
    list — into `projects/<project>/design/`, BEFORE any geometry exists.

    This is the design phase's only writer. A functional, wearable,
    multi-component or novel request does not go straight to geometry: it gets
    questions, a requirements sheet, a 2D concept diagram and a components
    proposal first, and then a sign-off gate. This tool is where each of those
    lands so it survives the conversation.

    - `project` is plain words ("ankle fan"); it becomes
      `projects/ankle-fan/design/`. Anything path-shaped is refused — nothing is
      ever written elsewhere. **The project folder does not have to exist yet**,
      and usually does not: the sheet comes before the part.
    - `filename` is one plain name with one of three extensions: `.md` (the
      requirements sheet, the mechanics notes, the components list), `.svg` (the
      concept diagram, which you write by hand — schematic boxes, lines,
      dimension callouts and text, not art), `.json` (structured numbers).
      Nothing that runs: a part script is `partforge_new_part`'s job, after the
      gate.
    - An `.svg` is parsed as XML and an `.json` as JSON before anything touches
      disk. A diagram cut off mid-tag draws as an empty box in the artist's
      chat, which reads as a broken tool — so a document that would not render
      is refused and no file is created.
    - Overwriting is normal. A design sheet iterates: the artist answers a
      question, a number changes, you save it again over the same name.

    Returns the path, the whole sheet as it now stands, and where the artist
    sees it. Name an `.svg`'s full path in your reply and the diagram renders
    inline in their chat.

    Nothing is built by this call, and nothing should be until they sign off.
    """
    slug = project_slug(project)
    name = design_filename(filename)
    design, path = design_paths(slug, name)
    text = normalize_design_content(content, name)

    try:
        design.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ForgeError(f"Could not write {path}: {exc}") from exc

    return fmt_design_saved(
        slug=slug,
        path=path,
        documents=design_documents(slug),
        overwritten=existed,
        settings=task_config.mention(slug),
        pipeline=pipeline.mention(slug),
    )


@app.tool()
def task_config_init(
    project: str,
    task: Literal["character", "part", "device", "floorplan", "mold"],
    force: bool = False,
) -> str:
    """Materialise the settings sheet for a project — every knob, pre-filled.

    **Do this in the design phase, before the geometry and before the
    questions are answered.** The sheet is
    `projects/<project>/design/task-config.json`, and what makes it worth
    having is that it arrives COMPLETE: every setting this kind of work has,
    already sitting at its default, with a clause saying why that default is
    the default. The artist then edits VALUES. They never have to know which
    fields exist, and you never have to guess a number they did not mention.

    The five tasks, and what each one is:

    - `character` — a rigged, animated body headed for a game engine.
      **`symmetry` defaults to `true`**: bipeds are symmetric unless the artist
      says otherwise, and an asymmetric one is an explicit choice recorded here,
      never an accident.
    - `part` — a parametric part headed for a printer (printer profile, wall
      thickness, whether bed fit constrains the design, export formats).
    - `device` — a part with a circuit in it; the battery, switch and LED
      choices are the real maker catalog's names.
    - `floorplan` — mirrors the plan defaults the level builder actually uses,
      so a ceiling height cannot mean two things.
    - `mold` — parting mode, shell, draft, registration keys, silicone.

    Refuses to overwrite an existing sheet: `force=true` rebuilds it from the
    template and throws away every value the artist has set. Changing ONE
    setting is `task_config_set`, which is almost always what was meant.

    Returns the whole sheet echoed back, every value in it. Show the artist the
    handful that matter to them and ask which to change — handing somebody a
    filled sheet is the point, and handing them a blank question is the thing
    this replaces.
    """
    slug = project_slug(project)
    kind = task_config.normalize_task(task)
    path = task_config.config_path(slug)

    replaced = path.is_file()
    if replaced and not force:
        try:
            current = task_config.read(slug)
            summary = ", ".join(task_config.changed(current)) or "none"
        except ForgeError:
            summary = "unreadable"
        raise ForgeError(
            f"{slug} already has a settings sheet at {path} (settings changed "
            f"from their defaults: {summary}). Rebuilding it from the template "
            "would throw those away. To change one setting, call "
            "task_config_set(project, name, value); to see what is on it, "
            "task_config_get(project). Pass force=true only if the artist asked "
            "to start the sheet over."
        )

    sheet = task_config.new_sheet(slug, kind)
    written = task_config.write(slug, sheet)
    return task_config.fmt_init(sheet=sheet, path=written, slug=slug,
                                replaced=replaced,
                                pipeline=pipeline.mention(slug))


@app.tool()
def task_config_get(project: str) -> str:
    """Read the project's settings sheet — EVERY value, and which are not default.

    Call this before doing work a setting governs, and call it again rather
    than remembering what it said. The sheet on disk is the truth; a value you
    are carrying from earlier in the conversation is a value the artist may have
    changed since.

    The report prints every setting whether or not anybody has touched it, with
    the ones that differ from their default marked. That is deliberate: the
    artist learns what they are allowed to control by watching the control
    surface come back filled in, and a report that listed only what somebody had
    already thought to mention would tell them exactly what they already knew.

    Writes nothing. A project with no sheet is told how to get one.
    """
    slug = project_slug(project)
    sheet = task_config.read(slug)
    return task_config.fmt_get(sheet=sheet, path=task_config.config_path(slug),
                               slug=slug, pipeline=pipeline.mention(slug))


@app.tool()
def task_config_set(project: str, name: str, value: Any) -> str:
    """Change ONE setting on the project's sheet, and echo the whole sheet back.

    **When the artist asks for something a setting governs, change the SETTING
    and say so.** "Make it asymmetric", "that's too heavy for mobile", "use
    PETG walls" — each of those is a value on this sheet, not a note to
    remember. A setting changed here is a setting every later tool reads; a
    number agreed in chat is a number the next turn loses.

    `value` is checked against what that setting can hold: a choice must be one
    of its choices, a number must be inside its range, a yes/no must be one.
    Anything else is refused in a sentence naming what would have worked, and
    the sheet is left exactly as it was.

    Setting `symmetry` to `false` on a character is a real decision, not a
    default drifting — say out loud that the body will no longer be mirrored
    and that the change is now on the sheet.

    Returns the sheet echoed back in full, with this setting marked as changed.
    """
    slug = project_slug(project)
    sheet = task_config.read(slug)
    before, after = task_config.apply(sheet, name, value)
    written = task_config.write(slug, sheet)
    settled = str(name).strip().strip('"').strip()
    if settled not in sheet["settings"]:
        settled = next(key for key in sheet["settings"]
                       if key.lower() == settled.lower())
    return task_config.fmt_set(sheet=sheet, path=written, slug=slug,
                               name=settled, before=before, after=after,
                               pipeline=pipeline.mention(slug))


@app.tool()
def pipeline_status(project: str) -> str:
    """The build's STAGE BOARD — what is done, what is red, what happens next.

    **A character build is a staged pipeline the artist drives step by step —
    "make the mesh", "check it", "now rig it" — never an assumed one-shot.**
    This is the board that makes that true across turns: ten stages for a
    character, five for a floor plan, each with the gate that decides it, the
    numbers that gate measured, and the files the stage produced.

    Call it at the START of any build turn, before doing work. The plan is read
    off disk every time — a stage verdict remembered from earlier in the
    conversation is a verdict the artist may have moved since, and the whole
    reason the board is a file is that the conversation is not one.

    The board reads as a column: `[x]` passed, `[!]` failed, `[>]` in progress,
    `[ ]` pending, `[~]` overridden (a red gate somebody signed their way past,
    with their name and reason on it). It ends with **next**, naming the exact
    call to make.

    - `project` is plain words ("werewolf"); the plan is
      `projects/<slug>/design/build-plan.json`.
    - Which stages exist comes from the project's **task** — the plan's own, or
      the settings sheet's. A project with neither is told to run
      `task_config_init` rather than handed a guessed chain.
    - A project with no plan yet still gets a board, materialised to be looked
      at and marked NOT ON DISK YET. Nothing is written by this call.

    Writes nothing, builds nothing, measures nothing. It is the state machine —
    you do the work between the stages and write the verdict back with
    `pipeline_record`.
    """
    slug, plan, path, fresh = pipeline.load(project)
    return pipeline.fmt_status(plan=plan, path=path, slug=slug, fresh=fresh)


@app.tool()
def pipeline_advance(
    project: str,
    stage: str,
    override: bool = False,
    who: str = "",
    why: str = "",
    cost: Optional[Dict[str, Any]] = None,
) -> str:
    """Start ONE stage of a staged build. Refuses to skip over a red gate.

    This is the "now rig it" call. It marks the stage in progress and writes the
    plan — it does not do the work, and it does not do the stages after it. Do
    that one stage, record what its gate measured, show the artist, and let
    them say go on.

    **A red gate blocks the next stage.** Advancing past a stage that failed, or
    over one that never finished, is refused in a sentence naming what is in the
    way and what it measured. That refusal is the feature: a rig fitted on a
    mesh that failed its verify gate is a rig that gets thrown away, and the
    stage board is what remembers the mesh failed.

    - `override=true` goes on anyway, and needs **`who`** and **`why`**. The
      stepped-over stage is then marked `overridden` — never `passed` — and both
      stages carry who signed it and what they said, for good. An override with
      no name and no reason is refused: it is a decision somebody made, and
      three sessions later that is exactly what matters.
    - Advancing to a stage that already passed re-opens it, which is how
      iteration works. The report says it was re-opened rather than started.
    - `cost` is optional and works exactly as it does on `pipeline_record`:
      `{"usd": 1.23, "tokens_in": 4000, "tokens_out": 900, "model": "sonnet"}`,
      accumulating onto the stage being started. It never overrides the
      ordering rules above — a red gate still refuses the advance, cost or no
      cost.

    Returns the whole board with this stage in progress, and one line saying
    what ending it looks like.
    """
    slug, plan, _path, _fresh = pipeline.load(project)
    report = pipeline.advance(plan, stage, override=override, who=who, why=why,
                              cost=cost)
    written = pipeline.write(slug, plan)
    return pipeline.fmt_advance(plan=plan, path=written, slug=slug,
                                report=report)


@app.tool()
def pipeline_record(
    project: str,
    stage: str,
    status: Literal["pending", "in_progress", "passed", "failed"],
    numbers: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Union[str, List[str]]] = None,
    replace: bool = False,
    cost: Optional[Dict[str, Any]] = None,
) -> str:
    """How a build turn writes its result in — the verdict, the numbers, the files.

    **Call this at the END of every stage, before you say anything to the
    artist.** A stage ends with its gate verdict and its artifacts; a turn that
    finished the work but recorded nothing leaves the next turn guessing, which
    is how a staged build quietly becomes a one-shot again.

    - `status` is what the gate actually answered: `passed`, `failed`, or
      `in_progress`/`pending` to put the stage back. `overridden` is not
      recordable — it is not a verdict anybody measures, it is what
      `pipeline_advance` writes when somebody signs past a red gate.
    - `numbers` is the gate's measurements, `{"worst_drift_mm": 1.1, "gate":
      "ok"}`. **A stage cannot pass with nothing measured**: a green verdict
      with no number beside it is an opinion, and the next stage has to be able
      to read what this one got. Keep them flat — deep detail belongs in a
      design document whose path is an artifact.
    - `artifacts` are the paths the stage produced — a .blend, a render, a
      .glb, exported STLs. Recorded paths are checked against disk and the board
      marks any that are not there: a path is a claim.
    - Numbers and artifacts MERGE into what the stage already carries, because a
      stage is usually measured by more than one tool. `replace=true` throws the
      previous ones away instead.
    - `cost` is what THIS call spent doing the stage's work — `{"usd": 1.23,
      "tokens_in": 4000, "tokens_out": 900, "model": "sonnet"}`. Unlike
      `numbers`/`artifacts`, it always ACCUMULATES onto the stage's running
      total regardless of `replace`, because a stage worked three times spent
      whatever those three turns cost, never just the last one. Each recording
      is also kept in the stage's history, so the individual spends stay
      auditable. Unknown keys, negative amounts, or a non-string `model` are
      refused.

    Record the failure honestly when the gate is red. A red stage blocks the
    stages after it, which is the point — the fix ladder is worked to green, or
    the failure itself is presented to the artist as the result.

    Returns the whole board with this stage settled, and names anything on the
    gate that nothing was recorded for.
    """
    slug, plan, _path, _fresh = pipeline.load(project)
    report = pipeline.record(plan, stage, status, numbers, artifacts,
                             replace=replace, cost=cost)
    written = pipeline.write(slug, plan)
    return pipeline.fmt_record(plan=plan, path=written, slug=slug, report=report)


@app.tool()
def partforge_open_in_panel(script_path: str) -> str:
    """Point Blender's Forge panel at a part script, so its sliders appear.

    Sets the scene's PartForge script path and rebuilds the parameter list from
    the script's PARAMS block, exactly as the panel's own Load Script button
    does — the artist gets working sliders without typing a path into a file
    field. Their panel and your tools are then looking at the same part.

    Builds nothing: follow with partforge_generate so the part is actually
    visible in the viewport.

    Needs Blender running with the Forge add-on server started; the geometry
    service supplies the parameter schema.
    """
    path = resolve_path(script_path, must_exist=True, label="script path")
    result = blender_client.send_command(
        "partforge_open", {"script_path": str(path)}
    )
    return fmt_open_report(path, result)


# ---------------------------------------------------------------------------
# PartForge print readiness (Phase 2)
# ---------------------------------------------------------------------------


def _joint_spec(
    joint_type: str, joint_tolerance: Optional[float]
) -> Dict[str, Any]:
    """The /segment `joint` object; tolerance omitted = the printer's own fit."""
    spec: Dict[str, Any] = {"type": joint_type}
    if joint_tolerance is not None:
        tolerance = float(joint_tolerance)
        if tolerance < 0.0:
            raise ForgeError(f"joint_tolerance cannot be negative; got {tolerance}.")
        spec["tolerance"] = tolerance
    return spec


def _segment_request(
    script_path: str,
    overrides: Optional[Dict[str, Any]],
    printer_path: Optional[str],
    joint_type: str,
    joint_tolerance: Optional[float],
    mode: SegmentMode,
) -> Dict[str, Any]:
    """Everything the three segmenting tools resolve the same way."""
    path, source = read_script(script_path)
    printer, printer_source = read_printer(printer_path)
    return {
        "path": path,
        "source": source,
        "printer": printer,
        "printer_source": printer_source,
        "overrides": overrides,
        "joint": _joint_spec(joint_type, joint_tolerance),
        "mode": normalize_segment_mode(mode),
    }


@app.tool()
def partforge_check(
    script_path: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
) -> str:
    """Can this part be printed? Bed fit, wall thickness, overhangs, watertightness.

    Runs the script through the geometry service against a printer profile and
    reports one verdict (pass/warn/fail) plus a line per check. Nothing is
    written and no mesh comes back, so this is the cheap first question to ask
    about any part before exporting it.

    When bed_fit FAILS the report carries the service's own suggested cut mode —
    hand that object straight to partforge_segment / partforge_load_segments /
    partforge_export_segments as `mode`. A bed_fit fail with a feasible split is
    NOT a design fault: the part is the size it should be and prints as N
    pieces, the report says so under a [SPLIT] tag, and nothing needs shrinking.
    Only an infeasible suggestion is a genuine problem.

    Two caveats worth repeating to the user: min_wall is inward ray casting on
    the mesh, so it is approximate (narrow gaps read as thin walls, a fail means
    "look here", not an exact dimension), and overhangs never fail — they warn,
    because supports exist.

    `printer_path` defaults to the repo's templates/printer.json and falls back
    to the service's built-in Elegoo Centauri Carbon profile when that file is
    missing; the report always names the profile it used.
    """
    path, source = read_script(script_path)
    printer, printer_source = read_printer(printer_path)
    payload = service_client.check(source, overrides, printer)
    return fmt_check_report(path.name, payload, overrides, printer_source)


@app.tool()
def partforge_segment(
    script_path: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
    joint_type: Literal["dovetail", "pin", "magnet", "none"] = "dovetail",
    joint_tolerance: Optional[float] = None,
    mode: SegmentMode = "auto",
) -> str:
    """Plan how to cut a too-big part into printable, joinable segments.

    Cuts the solid, fits a joint into every mating face, re-verifies each piece
    is watertight and packs them onto one plate — then reports the plan: segment
    names, kinds, oriented bounding boxes and the plate layout. Meshes are NOT
    requested, because this is the planning step and segment meshes are large;
    use partforge_load_segments to see the pieces, partforge_export_segments to
    write them.

    `mode` accepts partforge_check's suggested_segmentation.mode verbatim, or an
    integer radial count, or a list of Z heights. `joint_type` 'pin' adds the
    printed pins as extra segments of kind "hardware".

    A joint that cannot work on a face (a 6 mm magnet in a 2 mm wall, a dovetail
    over a hole) is an error that says what to use instead — read it and change
    the joint rather than retrying. `joint_tolerance` overrides the printer's
    press_fit / magnet_pocket_extra; `printer_path` works as in partforge_check.
    """
    request = _segment_request(
        script_path, overrides, printer_path, joint_type, joint_tolerance, mode
    )
    payload = service_client.segment(
        request["source"],
        request["overrides"],
        request["printer"],
        mode=request["mode"],
        joint=request["joint"],
        include_mesh=False,
    )
    report = fmt_segment_report(request["path"].name, payload, overrides)
    return f"{report}\n  printer: {request['printer_source']}"


@app.tool()
def partforge_load_segments(
    script_path: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
    joint_type: Literal["dovetail", "pin", "magnet", "none"] = "dovetail",
    joint_tolerance: Optional[float] = None,
    mode: SegmentMode = "auto",
    collection: Optional[str] = None,
) -> str:
    """Segment a part AND load every piece into Blender, laid out on the plate.

    Same cut as partforge_segment, but the meshes come back and each one is
    loaded as its own object (named after the segment, mesh data replaced in
    place on a re-run) positioned and spun exactly where the plate packing put
    it — so the viewport shows the print plate, not the assembled part.

    Use this to show the user what they will be printing. `collection` puts the
    pieces in a named collection instead of the scene collection. Needs Blender
    running with the Forge add-on server started; the cut still succeeds without
    it, the pieces just are not shown.
    """
    request = _segment_request(
        script_path, overrides, printer_path, joint_type, joint_tolerance, mode
    )
    payload = service_client.segment(
        request["source"],
        request["overrides"],
        request["printer"],
        mode=request["mode"],
        joint=request["joint"],
        include_mesh=True,
    )

    lines = [fmt_segment_report(request["path"].name, payload, overrides)]
    lines.append(f"  printer: {request['printer_source']}")

    placements = plate_items_by_name(payload.get("plate"))
    meshes: List[Dict[str, Any]] = []
    missing: List[str] = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        name = str(segment.get("name") or "segment")
        mesh = segment.get("mesh") or {}
        vertices = mesh.get("vertices") or []
        if not vertices:
            missing.append(name)
            continue
        item: Dict[str, Any] = {
            "name": name,
            "vertices": vertices,
            "faces": mesh.get("faces") or [],
        }
        if name in placements:
            item["plate"] = placements[name]
        meshes.append(item)

    lines.append("")
    if not meshes:
        lines.append("  NOT loaded into Blender: the service returned no segment meshes.")
        return "\n".join(lines)

    params: Dict[str, Any] = {"meshes": meshes, "replace": True}
    if collection and collection.strip():
        params["collection"] = collection.strip()

    try:
        loaded = blender_client.send_command("load_meshes", params)
    except BackendUnavailable as exc:
        lines.append(f"  NOT loaded into Blender — {exc}")
    except ForgeError as exc:
        lines.append(f"  Blender refused the segments — {exc}")
    else:
        objects = loaded.get("objects") or []
        where = f" in collection '{collection.strip()}'" if collection and collection.strip() else ""
        lines.append(
            f"  Loaded {len(objects)} object(s) into Blender{where}, "
            "positioned at their plate locations (mm -> m)."
        )
        for entry in objects:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"    {str(entry.get('object', '?')):<20.20} "
                f"{entry.get('vertex_count', '?')} verts, "
                f"{entry.get('face_count', '?')} faces"
            )
    if missing:
        lines.append(f"  no mesh returned for: {', '.join(missing)}")
    return "\n".join(lines)


@app.tool()
def partforge_export_segments(
    script_path: str,
    directory: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
    joint_type: Literal["dovetail", "pin", "magnet", "none"] = "dovetail",
    joint_tolerance: Optional[float] = None,
    mode: SegmentMode = "auto",
    basename: Optional[str] = None,
    format: Literal["stl", "step", "3mf"] = "stl",
) -> str:
    """Segment a part and write every piece to disk, plus a packed plate 3MF.

    One file per segment (and per printed pin), each already spun to its best
    orientation, centred and sitting on Z=0 so a slicer opens it ready to print,
    plus `<basename>_plate.3mf` holding them all at their packed plate positions.

    `directory` is created if missing; `basename` defaults to "part". Reports
    every file written with its size on disk.
    """
    request = _segment_request(
        script_path, overrides, printer_path, joint_type, joint_tolerance, mode
    )
    out_dir = resolve_path(directory, label="output directory")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ForgeError(f"Could not create the output directory {out_dir}: {exc}") from exc

    payload = service_client.export_segments(
        request["source"],
        request["overrides"],
        request["printer"],
        mode=request["mode"],
        joint=request["joint"],
        directory=str(out_dir),
        basename=(basename or "").strip() or None,
        fmt=format.lower(),
    )

    files = payload.get("files") or []
    lines = [
        f"Exported {request['path'].name} as {format.upper()} segments to "
        f"{payload.get('directory', out_dir)}",
        f"  mode: {fmt_mode(payload.get('mode'))}   "
        f"joint: {fmt_joint(payload.get('joint'))}   "
        f"printer: {request['printer_source']}",
        "",
        fmt_written_files(files, payload.get("plate_path")),
    ]
    plate = payload.get("plate")
    if isinstance(plate, dict) and not plate.get("fits", True):
        lines.append("  WARNING: the packed plate does not fit the bed.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Maker mode (Phase 10) — the part that does something when you press it
#
# These four are the only tools in this server that do not go over a wire. They
# read service/components.py, service/wiring.py and the arithmetic half of
# service/maker_lib.py straight off disk — forge_mcp/maker.py's module docstring
# argues that at length. Geometry is NOT here: a maker part is built the way
# every PartForge part is built, with a PARAMS script that composes maker_lib.
# These tools are the half before the script exists (what to buy, what will
# fit, how far it moves) and the half after it is printed (what to solder).
# ---------------------------------------------------------------------------


@app.tool()
def maker_components(filter: Optional[str] = None) -> str:
    """What real parts can this design be built around? START HERE, before geometry.

    Answers "what should I buy" with 18 actual, purchasable components — every
    switch, cell, LED, screw and magnet the maker library knows the datasheet
    numbers for. Each entry carries what it is in one sentence, the dimensions
    that set the model's dimensions, a **purchase note** (the search term that
    finds the right one, and the near-identical wrong one to avoid), and the
    `verify_against_your_part` sentence the artist has to hear before they print.

    **The law this tool exists to enforce: pick the real part FIRST, then model
    to its dimensions.** A cavity invented from nothing fits nothing. Never write
    a bare pocket and hope a switch drops into it — ask here, then let the
    numbers that come back set the numbers in the script.

    `filter` takes a category (`switch`, `power`, `light`, `fastener`, `magnet`),
    an exact component name (`tactile_6x6_latching` — that gives the full card:
    every dimension, where its Z = 0 datum sits, its mount styles, its lead
    layout), or free text matched against names and summaries. No filter lists
    the lot, grouped by category.

    Dimensions are datasheet-typical for the family, not a measurement of one
    unit: clones vary by ±0.3 mm routinely. Say that out loud — it is honesty,
    not hedging, and it is why the verify sentence exists.

    The workflow this starts: **maker_components** → `circuit_plan` (does it need
    a resistor?) and `plunger_plan` (how far does the press move?) → read
    `docs/part-authoring.md` §7 → `partforge_new_part` with a script composing
    `maker_lib.cutout` / `mount` / `plunger` → `partforge_generate` →
    `partforge_check` **on each piece in its own orientation** → `wiring_guide`
    at handover. A maker housing is two pieces by necessity (§7.7 says why).
    """
    result = maker.catalog(filter)
    return fmt_component_catalog(result, maker.clone_tolerance_mm())


@app.tool()
def circuit_plan(
    led: str = "led_5mm",
    color: Optional[str] = None,
    cell: str = "cr2032_cell",
    cells: int = 1,
    switch: Optional[str] = "tactile_6x6_latching",
    current_ma: Optional[float] = None,
) -> str:
    """Does this LED need a resistor, and which one? Ohm's law over real datasheets.

    A printed housing with a perfect switch pocket is still not a lamp. Run this
    the moment a design has a light in it, and state the verdict in the reply —
    it is one of three, and the surprising one is right more often than people
    expect:

    - **no resistor needed** — the supply has nothing left to drop. A white or
      blue LED (3.0 V) on a CR2032 (3.0 V) is this case, and the cell's own ~30 Ω
      internal resistance is the real current limit. This is the common answer
      for a glowing figure, so do not "add a resistor to be safe" — say why there
      is none.
    - **resistor optional** — small headroom on a supply that limits itself. The
      plan names the trade: brighter now, flat far sooner.
    - **resistor required** — everything else. Two cells and a red LED wants
      220 Ω. Swapping a coin cell for `aaa_pair_box` turns optional into
      required, because two AAAs *will* deliver the 20 mA that kills the LED.

    Values are rounded **up** to the next E12 value, because too big only dims
    the LED and too small cooks it. The plan also reports `resistor_gentle_ohms`
    — the same maths at the *cell's* recommended current instead of the LED's
    nominal 20 mA — with the run time for both, because the textbook answer and
    the answer that is still lit tomorrow are often different numbers.

    `led` / `cell` / `switch` are names from `maker_components`; `color` is the
    LED colour (it sets the forward voltage, and it is the whole reason white and
    red give different verdicts); `cells` is how many in series; `current_ma`
    overrides the target current. `switch=null` plans a circuit with no switch.

    Follow with `wiring_guide` when the artist is ready to solder.
    """
    plan = maker.circuit(
        led,
        color=color,
        cell=cell,
        cells=cells,
        switch=switch,
        current_ma=current_ma,
    )
    return fmt_circuit_plan(plan)


@app.tool()
def wiring_guide(
    led: str = "led_5mm",
    color: Optional[str] = None,
    cell: str = "cr2032_cell",
    cells: int = 1,
    switch: Optional[str] = "tactile_6x6_latching",
    current_ma: Optional[float] = None,
) -> str:
    """What do I solder, in what order, and what do I buy? The handover document.

    `circuit_plan`'s verdict plus **13 numbered beginner steps** in the order the
    mistakes actually happen in — find the LED's long leg before trimming
    anything, find the switch's terminal pairs with a meter before soldering,
    one series loop with no branches, every joint insulated — and a **shopping
    list** where every line carries the search term that finds the right part.

    **End every functional build with this.** A housing whose owner cannot wire
    it is an ornament. Give it as the last thing, after the pieces are checked
    and the artist knows what to print.

    Two steps must reach them intact, never paraphrased away:

    - **polarity** — the LED's longer leg is positive, and the flat filed on the
      plastic rim marks the negative one. Backwards is not dangerous; it simply
      does not light, and it is the single commonest reason a first circuit does
      nothing.
    - **test it on the bench before a single drop of glue** — cell in, switch
      pressed, watching the LED. Glue is the point of no return, and nineteen
      failures in twenty are the LED round the wrong way, the cell upside down,
      or the wrong pair of switch terminals.

    Arguments are `circuit_plan`'s. Say the run time out loud too — it is in
    there, and "roughly 23 hours from one coin cell" is the kind of number that
    decides whether the battery door needs to be easy to open.
    """
    guide = maker.wiring_guide(
        led,
        color=color,
        cell=cell,
        cells=cells,
        switch=switch,
        current_ma=current_ma,
    )
    return fmt_wiring_guide(guide)


@app.tool()
def plunger_plan(
    stem_diameter: float,
    switch: str = "tactile_6x6_latching",
    guide_length: Optional[float] = None,
    overtravel: Optional[float] = None,
    keyed: bool = True,
) -> str:
    """How far does the press move, what stops it, and what pushes it back?

    A gap that makes a cap removable is not a mechanism. This is the arithmetic
    behind one that is: a printed pin in a printed sleeve that reaches a real
    switch, with a retention flange so it cannot fall out the front and an end
    stop so a finger's 5 kg never reaches a switch rated for 250 gf.

    Every number comes from the switch's own datasheet and the printer profile —
    nothing is a constant typed into a script. In particular **overtravel is
    clamped by the component**, and the plan lists every clamp it applied.
    Repeat those clamps in the reply; do not swallow them.

    Say the result to the artist as a feeling with numbers in it, not a table:
    *"the flame presses 1.8 mm and the switch's own spring pushes it back."*
    Travel, what returns it (a latching switch has no return spring while it is
    latched, so the cap visibly sits lower while the light is on — a feature,
    worth saying), where it stops, and the press force in grams.

    Two switches refuse a plunger and say what to use instead: a slide switch
    moves sideways, and a 12 mm panel-mount push button already *is* the plunger.

    `stem_diameter` is the pin's diameter in mm (4 mm or more if a finger pushes
    it). `keyed` flats the stem so a shaped cap cannot spin on it.

    **This tool does the arithmetic; it builds nothing.** The solid comes from
    `maker_lib.plunger()` inside a PARAMS script — `docs/part-authoring.md` §7.4
    — built by `partforge_generate`.
    """
    plan = maker.plunger(
        stem_diameter,
        switch=switch,
        guide_length=guide_length,
        overtravel=overtravel,
        keyed=keyed,
    )
    return fmt_plunger_plan(plan)


# ---------------------------------------------------------------------------
# Imported models (Phase 6d) — "I downloaded this STL, will it print?"
# ---------------------------------------------------------------------------

#: A mesh with holes cannot be measured or cut, and the geometry service says so
#: in one plain sentence. That sentence is the truth; this is the fix, and it is
#: the same fix on both tools.
_REPAIR_ADVICE = (
    "Fix it first: remesh(mode=\"voxel\") on that object rebuilds the surface as "
    "one closed shell, sealing holes and non-manifold edges (the artist's own "
    "button for this is the Forge panel's Model box > Voxel Repair). Start with "
    "the default 0.01 m voxel and only go smaller if detail is lost — smaller is "
    "much heavier. Then run the same call again."
)

#: What a "repair it first" refusal from the service looks like by the time it
#: has crossed the add-on's socket. Matched loosely on purpose: the exact
#: wording is the service's, and it must keep reaching the caller verbatim.
_REPAIR_MARKERS = ("watertight", "repair first", "not closed", "manifold")


def _with_repair_advice(exc: ForgeError) -> ForgeError:
    """Re-dress a non-watertight refusal with the fix, message kept word for word.

    A backend that is simply not running is left alone — "start Blender" is
    already the right advice there, and voxel-remeshing nothing would not help.
    """
    if isinstance(exc, BackendUnavailable):
        return exc
    text = str(exc)
    if not any(marker in text.lower() for marker in _REPAIR_MARKERS):
        return exc
    return BackendError(f"{text}\n\n{_REPAIR_ADVICE}")


def _model_request(
    object_name: Optional[str], printer_path: Optional[str]
) -> tuple[Dict[str, Any], str]:
    """Target + printer, resolved the way every print-readiness tool resolves them.

    The profile only goes on the wire when there IS one, so "no profile" keeps
    meaning "use the service's own Centauri Carbon defaults" rather than an empty
    object to merge over them.
    """
    params = _target(object_name)
    printer, printer_source = read_printer(printer_path)
    if printer:
        params["printer"] = printer
    return params, printer_source


@app.tool()
def check_model(
    object: Optional[str] = None,
    printer_path: Optional[str] = None,
) -> str:
    """Can this DOWNLOADED or imported model be printed? Bed fit, walls, overhangs.

    The raw-mesh twin of partforge_check, and the one to reach for when there is
    no script: an STL or OBJ the artist downloaded, a sculpt they made, anything
    that arrived as triangles. partforge_check needs a PARAMS script; this needs
    only an object that is already in the Blender scene.

    The add-on takes that object's evaluated mesh (modifiers applied), scales
    Blender's metres to millimetres and asks the geometry service the same four
    questions — bed fit, minimum wall, overhangs, watertightness — then writes the
    rows into the panel's Print Checks box, so the artist reads the same verdict
    you do.

    `object` is the object's name; omit it for Blender's active object.
    `printer_path` works exactly as in partforge_check: the repo's
    templates/printer.json by default, the service's built-in Elegoo Centauri
    Carbon profile when that file is missing, and the report always names which
    profile it used.

    Two things read differently on an imported mesh than on a PartForge part:

    - There is no B-Rep, so solid validity is never reported — "watertight" here
      is the triangle mesh's own closedness, nothing more.
    - A model that is NOT watertight is REFUSED rather than checked, with a plain
      "repair first" message. That is not a bug and not worth retrying: tell the
      artist their download has holes, run remesh(mode="voxel") on it (the panel's
      Model box has a Voxel Repair button that does the same), then check again.

    When bed_fit fails the report carries the suggested cut mode — hand that
    object straight to segment_model as `mode`. A model bigger than the bed is
    not a broken model: with a feasible split the report tags it [SPLIT] and
    says how many pieces it prints as, which is print planning, not a fault to
    fix. And the same two caveats as partforge_check apply: min_wall is
    approximate inward ray casting ("look here", not an exact dimension), and
    overhangs warn rather than fail.
    """
    params, printer_source = _model_request(object, printer_path)
    try:
        result = blender_client.send_command(
            "check_model",
            params,
            read_timeout=max(config.BLENDER_READ_TIMEOUT, config.SERVICE_CHECK_TIMEOUT),
        )
    except ForgeError as exc:
        raise _with_repair_advice(exc) from exc
    return fmt_model_check_report(object, result, printer_source)


@app.tool()
def segment_model(
    object: Optional[str] = None,
    printer_path: Optional[str] = None,
    joint_type: Literal["dovetail", "pin", "magnet", "none"] = "dovetail",
    joint_tolerance: Optional[float] = None,
    mode: SegmentMode = "auto",
    collection: Optional[str] = None,
) -> str:
    """Cut a DOWNLOADED or imported model into printable, joinable pieces.

    The raw-mesh twin of partforge_load_segments: it cuts the mesh that is
    already in Blender — a download, a sculpt, anything with no PARAMS script
    behind it — fits a joint into every mating face, re-checks each piece is
    watertight, packs them onto one plate AND loads the pieces back into the
    viewport where the packing put them. One call, and the artist sees their
    print plate instead of the assembled model.

    - `object`: the model to cut; omit it for Blender's active object.
    - `mode`: "auto", an integer radial wedge count, a list of Z heights, or
      check_model's own suggested_segmentation.mode copied verbatim.
    - `joint_type`: "dovetail" (default), "pin" (the printed pins come back as
      extra "hardware" pieces), "magnet", or "none". `joint_tolerance` overrides
      the printer profile's press_fit / magnet_pocket_extra.
    - `collection`: put the pieces in a named Blender collection instead of the
      scene collection — worth doing, because a cut model is a lot of objects.
    - `printer_path`: as in check_model.

    Run check_model first: its bed_fit failure is what says a cut is needed, and
    its suggestion is the `mode` to use. A model that is not watertight is
    refused here too — remesh(mode="voxel") to repair it, then cut.

    Meshes never reach you: the pieces travel service -> Blender and the report
    names the objects that landed, their oriented sizes and whether the packed
    plate fits the bed. A joint that cannot work on a face (a 6 mm magnet in a
    2 mm wall) is an error saying what to use instead — change the joint rather
    than retrying.
    """
    params, printer_source = _model_request(object, printer_path)
    params["joint"] = _joint_spec(joint_type, joint_tolerance)
    params["mode"] = normalize_segment_mode(mode)
    if collection and collection.strip():
        params["collection"] = collection.strip()

    try:
        result = blender_client.send_command(
            "segment_model",
            params,
            read_timeout=max(
                config.BLENDER_READ_TIMEOUT, config.SERVICE_SEGMENT_TIMEOUT
            ),
        )
    except ForgeError as exc:
        raise _with_repair_advice(exc) from exc
    return fmt_model_segment_report(object, result, printer_source, collection)


# ---------------------------------------------------------------------------
# Molds and casting (Phase 12) — "print the mold, pour twenty copies"
#
# The geometry service has had four mold routes and a real undercut analysis
# since Phase 12; until now nothing on this surface reached them, so the only
# way in was to read Forge's source and hand-write a flow (dogfood
# 2026-09-16, G-1). These two tools are that lane:
#
#   undercut_check  — will it come out of a mold at all? Writes nothing.
#   make_mold       — the mold itself, filed in projects/<slug>/molds/.
#
# Both take the SAME three inputs and route themselves: a PARAMS script goes to
# /mold /export_mold, a mesh (a generated figure, a download, an object in the
# Blender scene) goes to /mold_mesh /export_mold_mesh. The mesh never crosses
# this wire — an object in Blender is exported to a scratch STL in millimetres
# by the add-on's own export_stl, and the service reads that file itself.
# ---------------------------------------------------------------------------

#: A mold is only as good as the draw it was analysed against, and the service
#: refuses the same two things /segment_mesh does — a mesh with holes and a mesh
#: past the triangle ceiling. Both are re-dressed with _with_repair_advice, so a
#: "repair first" reaching the artist reads the same here as everywhere else.
_MOLD_INPUTS = ("script_path", "object", "mesh_path")


def _mold_input(
    script_path: Optional[str],
    object_name: Optional[str],
    mesh_path: Optional[str],
) -> Dict[str, Any]:
    """Exactly one of the three inputs, resolved before anything goes on a wire.

    Returns `{"kind", "subject", "source"?, "path"?, "file_path"?}`. A Blender
    object is exported here, not in the service call, so a scene with nothing in
    it costs one socket round trip rather than a mold job.
    """
    given = [
        name
        for name, value in zip(_MOLD_INPUTS, (script_path, object_name, mesh_path))
        if value is not None and str(value).strip()
    ]
    if not given:
        raise ForgeError(
            "Nothing to mold. Pass `script_path` for a PartForge part, "
            "`mesh_path` for a file on disk (an .stl/.obj/.3mf — a generated "
            "figure or a download), or `object` for something already in the "
            "Blender scene."
        )
    if len(given) > 1:
        raise ForgeError(
            f"Pass ONE input, not {len(given)} ({', '.join(given)}). The mold "
            "comes off one thing: the script, the mesh file, or the Blender "
            "object."
        )

    if script_path and str(script_path).strip():
        path, source = read_script(script_path)
        return {"kind": "script", "subject": path.name, "path": path, "source": source}

    if mesh_path and str(mesh_path).strip():
        path = resolve_path(mesh_path, label="mesh path", must_exist=True)
        return {"kind": "mesh", "subject": path.name, "file_path": str(path)}

    name = str(object_name).strip()
    # The add-on scales Blender's metres to millimetres on the way out (its
    # `scale` default is 1000), which is the unit every number in the service
    # is in. That is the whole reason this goes through export_stl rather than
    # through a mesh payload assembled here.
    out = mold_input_path(name)
    blender_client.send_command("export_stl", {"objects": [name], "path": str(out)})
    return {"kind": "mesh", "subject": name, "file_path": str(out), "from_blender": True}


def _mold_options(
    mode: str,
    parting_z_mm: Optional[float],
    draft_deg: Optional[float],
    shell_mm: Optional[float],
    clearance_mm: Optional[float],
    registration_keys: Optional[int],
    spout_diameter_mm: Optional[float],
    vents: Optional[int],
    undercut_threshold_deg: Optional[float],
    margin_mm: Optional[float] = None,
    pour_clearance_mm: Optional[float] = None,
    split: Optional[bool] = None,
) -> Dict[str, Any]:
    """The mold option block, checked here for the things arithmetic cannot fix.

    Everything the SERVICE validates (draft against the wall angle, a spout that
    will not fit inside the cavity) stays the service's job and comes back as
    its own 400. What is refused here is only what would be a round trip wasted:
    a negative dimension, and a key count that is not a whole number.
    """
    options: Dict[str, Any] = {"mode": normalize_mold_mode(mode)}

    for name, value in (
        ("draft_deg", draft_deg),
        ("shell_mm", shell_mm),
        ("clearance_mm", clearance_mm),
        ("margin_mm", margin_mm),
        ("pour_clearance_mm", pour_clearance_mm),
        ("undercut_threshold_deg", undercut_threshold_deg),
    ):
        if value is None:
            continue
        number = float(value)
        if number < 0.0:
            raise ForgeError(f"{name} cannot be negative; got {number:g}.")
        options[name] = number

    if parting_z_mm is not None:
        options["parting_z_mm"] = float(parting_z_mm)

    if registration_keys is not None:
        count = int(registration_keys)
        if count < 0:
            raise ForgeError(
                f"registration_keys cannot be negative; got {count}. 0 means no "
                "keys, and then the halves line up by eye — worth saying out "
                "loud, because a mold that shifts casts a seam."
            )
        options["registration_keys"] = count

    if spout_diameter_mm is not None:
        diameter = float(spout_diameter_mm)
        if diameter < 0.0:
            raise ForgeError(
                f"spout_diameter_mm cannot be negative; got {diameter:g}. Pass 0 "
                "for a mold with no spout (you pour along one edge instead)."
            )
        options["spout"] = False if diameter == 0.0 else {"diameter_mm": diameter}

    if vents is not None:
        count = int(vents)
        if count < 0:
            raise ForgeError(f"vents cannot be negative; got {count}.")
        options["vents"] = count

    if split is not None:
        options["split"] = bool(split)
    return options


def _mold_printer(printer_path: Optional[str]) -> tuple[Optional[Dict[str, Any]], str]:
    """The printer profile, resolved the way every print-readiness tool does."""
    return read_printer(printer_path)


@app.tool()
def undercut_check(
    script_path: Optional[str] = None,
    object: Optional[str] = None,
    mesh_path: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
    mode: Literal["printed_negative", "master_box"] = "printed_negative",
    parting_z_mm: Optional[float] = None,
    undercut_threshold_deg: Optional[float] = None,
) -> str:
    """Will this come OUT of a mold? Run this BEFORE make_mold. Writes nothing.

    The first question in the casting lane and the one nobody asks until the
    silicone has set: after the mold is split, does each half lift straight off,
    or does the part hang back over it? The service measures every triangle
    against the draw direction of the half it belongs to and answers per half —
    `none`, `mild` or `severe` — with the patch area, the worst angle past
    vertical, how deep the sideways grip is, and located example faces to go and
    look at.

    Give it ONE of:

    - `script_path` — a PartForge part (the CAD lane). `overrides` applies the
      same way as everywhere else.
    - `mesh_path` — an .stl/.obj/.3mf on disk: a generated figure, a download, a
      sculpt somebody exported. Millimetres, watertight.
    - `object` — something already in the Blender scene; it is exported to a
      scratch STL in millimetres and the service reads that.

    **The verdict decides the mold, so read it before choosing one.** `none` or
    `mild` means two printed halves work (`make_mold()`, the default). `severe`
    means a rigid half cannot come off the part at all, and the answer is not a
    better parting plane — it is `make_mold(mode="master_box")`: print the
    figure, print an open box round it, pour silicone in, and let the rubber
    flex off the undercut. The report says which, and `recommend_master_box` is
    the field it says it with.

    Two honesty notes that must survive into your reply. The angles and areas
    are MEASURED; the mild/severe line between them is a THRESHOLD somebody
    chose for typical tin/platinum silicone — quote the number beside the band.
    And depth is approximated as a radial bulge about the part's vertical axis,
    so a lobe sitting off-centre in plan can over-report.

    `parting_z_mm` forces the split height (the default is the part's widest
    horizontal cross-section, which is almost always right).
    `undercut_threshold_deg` is how far past vertical counts as opposing at all;
    the default, 1 degree, is deliberately strict.
    """
    resolved = _mold_input(script_path, object, mesh_path)
    printer, printer_source = _mold_printer(printer_path)
    options = _mold_options(
        mode,
        parting_z_mm,
        None,
        None,
        None,
        None,
        None,
        None,
        undercut_threshold_deg,
    )

    try:
        if resolved["kind"] == "script":
            payload = service_client.mold(
                resolved["source"],
                overrides,
                printer,
                options=options,
                include_mesh=False,
            )
        else:
            payload = service_client.mold_mesh(
                file_path=resolved["file_path"],
                printer=printer,
                options=options,
                include_mesh=False,
            )
    except ForgeError as exc:
        raise _with_repair_advice(exc) from exc

    report = fmt_undercut_report(resolved["subject"], payload)
    return f"{report}\n  printer: {printer_source}"


@app.tool()
def make_mold(
    project: str,
    script_path: Optional[str] = None,
    object: Optional[str] = None,
    mesh_path: Optional[str] = None,
    name: Optional[str] = None,
    mode: Literal["printed_negative", "master_box"] = "printed_negative",
    overrides: Optional[Dict[str, Any]] = None,
    printer_path: Optional[str] = None,
    parting_z_mm: Optional[float] = None,
    draft_deg: Optional[float] = None,
    shell_mm: Optional[float] = None,
    clearance_mm: Optional[float] = None,
    registration_keys: Optional[int] = None,
    spout_diameter_mm: Optional[float] = None,
    vents: Optional[int] = None,
    margin_mm: Optional[float] = None,
    pour_clearance_mm: Optional[float] = None,
    split: Optional[bool] = None,
    format: Literal["stl", "step", "3mf"] = "stl",
) -> str:
    """Build a mold from a part or a mesh and write it into projects/<project>/molds/.

    **Run `undercut_check` first.** It is the same geometry and it costs one
    call, and its verdict is what chooses between this tool's two modes.

    - `mode="printed_negative"` (default) — two printed halves with the part cut
      out of them, mated by registration keys, with a pour spout and vents. You
      print the halves, clamp them, and pour resin in. Good for a handful of
      pulls; the parting line sands flush.
    - `mode="master_box"` — the figure itself, untouched, plus an open box to
      glue it into and pour SILICONE around. That is the answer to severe
      undercuts and the answer to "I want twenty of these", because rubber
      flexes off what a rigid half grips. `split=true` cuts the box in two with
      keys so the cured rubber demolds easily.

    Give it ONE input, the same three `undercut_check` takes: `script_path` (a
    PartForge part), `mesh_path` (a generated figure or a download on disk), or
    `object` (something in the Blender scene, exported to a scratch STL in mm).

    `project` is the project the mold belongs to and the ONLY thing that decides
    where files land — `projects/<slug>/molds/`, beside the part they came off.
    There is no path parameter on purpose: a mold written to a path the model
    invented is a mold the artist cannot find. `name` is the file stem
    (`"litwick flame"` → `litwick-flame_mold_top.stl`), never a path.

    The geometry knobs are all optional and all defaulted by the service:
    `parting_z_mm` ("auto" = the widest cross-section), `draft_deg` (2),
    `shell_mm` (4 mm of wall per side), `clearance_mm` (grow the cavity all
    round, for a tight-fitting casting), `registration_keys` (4; 0 for none),
    `spout_diameter_mm` (0 for no spout), `vents` (auto). The master_box ones
    are `margin_mm` (silicone around the figure, 10), `pour_clearance_mm` (15
    above its highest point) and `split`.

    **The reply must name every file path this writes** — they are the whole
    deliverable, and a path the artist is not shown is a file they do not have.
    The report also carries the service's own casting instructions, start to
    finish, and they are written for somebody who has never poured a mold: hand
    them over rather than summarising them away. Print settings matter here in a
    way they do not for an ordinary part (fine layers, no supports in the
    cavity, and sand the cavity faces), and every layer line in the cavity shows
    up on every copy.

    Silicone and casting resin are not printed parts: end the turn by saying
    what they still have to buy, with the link, as a search — and never buy
    anything or offer to.
    """
    resolved = _mold_input(script_path, object, mesh_path)
    printer, printer_source = _mold_printer(printer_path)
    options = _mold_options(
        mode,
        parting_z_mm,
        draft_deg,
        shell_mm,
        clearance_mm,
        registration_keys,
        spout_diameter_mm,
        vents,
        None,
        margin_mm=margin_mm,
        pour_clearance_mm=pour_clearance_mm,
        split=split,
    )

    # Resolved and created BEFORE the job is submitted, so a bad project name
    # costs nothing — the same rule generate_3d states out loud.
    out_dir = project_molds_dir(project)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ForgeError(f"Could not create the molds directory {out_dir}: {exc}") from exc
    basename = mold_basename(name or resolved["subject"].rsplit(".", 1)[0])

    try:
        if resolved["kind"] == "script":
            payload = service_client.export_mold(
                resolved["source"],
                overrides,
                printer,
                options=options,
                directory=str(out_dir),
                basename=basename,
                fmt=format.lower(),
            )
        else:
            payload = service_client.export_mold_mesh(
                file_path=resolved["file_path"],
                printer=printer,
                options=options,
                directory=str(out_dir),
                basename=basename,
                fmt=format.lower(),
            )
    except ForgeError as exc:
        raise _with_repair_advice(exc) from exc

    return fmt_mold_report(resolved["subject"], payload, out_dir, printer_source)


# ---------------------------------------------------------------------------
# Phase 11 — base shapes: the artist draws it, and many pieces become one
# ---------------------------------------------------------------------------

#: A voxel remesh of a big union is a minute of main-thread work — the same
#: reasoning as the generated-mesh import below.
_MERGE_TIMEOUT = 300.0


@app.tool()
def profile_from_curve(
    curve_object: str,
    points: Optional[int] = None,
    close_bottom: bool = True,
) -> str:
    """Read a curve the artist DREW into (radius, z) points for forge_lib.soft_body.

    The third door into a base shape. They can describe it ("a bowl that swells
    at the shoulder"), show you a picture, or draw the silhouette themselves —
    this is the drawn one, and it is the most exact of the three because the
    shape is theirs to begin with.

    Tell them: press Numpad 1 for the front view, Add > Curve > Bezier, draw the
    RIGHT-HAND EDGE of the silhouette going up from the base, and tell you what
    the object is called. Radius is measured from the world Z axis (the blue
    vertical line at the origin), so that axis is the model's centre line.

    Nothing is built and nothing in the scene changes. What comes back is 5-10
    control points — write them into a PARAMS script with partforge_new_part, so
    what the artist drew is still a PARAMETRIC part with sliders on it and not a
    mesh frozen at the moment they drew it. That is the whole point of the door.

    - `points`: how many control points to return (5-10, default 7). The
      simplification keeps the silhouette's own extremes — the shoulder, the
      waist, the rim — and drops the samples in between.
    - `close_bottom` (default true): drop the profile onto z = 0 so the body
      stands on the build plate.

    A silhouette can only climb: samples that double back downward are dropped
    and counted in the notes, because a body of revolution cannot overhang
    itself. An open curve is fine here — for a closed loop (an ear, a fin) use
    outline_from_curve instead.
    """
    params: Dict[str, Any] = {
        "curve_object": curve_object,
        "close_bottom": bool(close_bottom),
    }
    if points is not None:
        params["points"] = int(points)
    result = blender_client.send_command("profile_from_curve", params)
    return fmt_profile_report(result)


@app.tool()
def outline_from_curve(
    curve_object: str,
    points: Optional[int] = None,
    recenter: bool = True,
) -> str:
    """Read a CLOSED drawn curve into [x, y] points for forge_lib.silhouette_part.

    The same door, for the pieces that hang off a body: an ear, a fin, a tail, a
    wing, a crest. The artist draws the outline as a closed loop (Add > Curve >
    Bezier, then Alt+C in Edit Mode to close it) and this measures it into the
    6-16 control points silhouette_part splines and extrudes.

    Nothing is built. Feed the points into a PARAMS script — with a thickness,
    and a `peg` when the piece plugs into a socket on the core — and the drawn
    ear becomes a parametric one.

    - `points`: 6-16, default 12. Eight to sixteen is the useful band.
    - `recenter` (default true): centre the outline on x = 0 with its bottom on
      y = 0, which is where silhouette_part attaches the peg.

    An open curve is refused with the two keys that close it, because an outline
    with a gap in it has no inside to fill.
    """
    params: Dict[str, Any] = {
        "curve_object": curve_object,
        "recenter": bool(recenter),
    }
    if points is not None:
        params["points"] = int(points)
    result = blender_client.send_command("outline_from_curve", params)
    return fmt_outline_report(result)


@app.tool()
def merge_for_print(
    objects: Optional[List[str]] = None,
    collection: Optional[str] = None,
    voxel_size_mm: Optional[float] = None,
    name: Optional[str] = None,
    keep_originals: bool = True,
) -> str:
    """Join the pieces the artist KEPT into ONE watertight shell for the slicer.

    The last step of a component-tree design. The core and its proposals are
    separate objects the whole time the artist is deciding — which is what makes
    "scrap the collar" one delete — and separate objects are exactly wrong at
    the slicer, where two overlapping solids are two objects with a seam.

    What it does: copies the chosen meshes, joins them, and voxel-remeshes the
    union into a single sealed surface.

    - `objects`: the names to merge. Omit them and name a `collection` instead —
      every VISIBLE mesh in it goes in, which is why scrapping a proposal is a
      delete or a hide and nothing else. Omit both and it merges the selection.
    - `voxel_size_mm`: omit it. The default is half the printer's nozzle
      (0.2 mm on a 0.4 mm nozzle) — two voxels per bead, which resolves every
      detail the printer could actually lay down and spends nothing on detail it
      could not. Finer costs file size quadratically; coarser is a real choice
      worth offering when the file matters more than the surface.
    - `name`: default `<project>-merged`, by the component naming convention.
    - `keep_originals` (default true): the pieces are HIDDEN, not deleted. Say
      so — it is what makes the merge safe to try.

    Say the trade out loud when you report it: a voxel merge rounds off detail
    finer than the voxel, and thin sculpted details (a whisker, a fingernail)
    are what it takes first. Then call **check_model** on what came back, and if
    a check fails run mesh_diagnose — it gives the millimetre coordinates of the
    thin places, so the artist knows where to thicken instead of guessing.
    """
    params: Dict[str, Any] = {"keep_originals": bool(keep_originals)}
    names = [entry.strip() for entry in (objects or []) if entry and entry.strip()]
    if names:
        params["objects"] = names
    if collection and collection.strip():
        params["collection"] = collection.strip()
    if voxel_size_mm is not None:
        params["voxel_size_mm"] = float(voxel_size_mm)
    if name and name.strip():
        params["name"] = name.strip()

    result = blender_client.send_command(
        "merge_for_print",
        params,
        read_timeout=max(config.BLENDER_READ_TIMEOUT, _MERGE_TIMEOUT),
    )
    return fmt_merge_report(result)


# ---------------------------------------------------------------------------
# Phase 15 — the project's own scene file
# ---------------------------------------------------------------------------

#: Writing (or loading) a whole sculpt is disk work on Blender's main thread.
#: The same budget the bridge gives these two routes (BLEND_SAVE_TIMEOUT /
#: BLEND_OPEN_TIMEOUT), so the two surfaces wait the same length of time for
#: the same command.
_BLEND_TIMEOUT = 300.0


@app.tool()
def save_project_blend(project: Optional[str] = None) -> str:
    """Save the Blender scene into the project as `projects/<name>/<name>.blend`.

    The home for everything a part script cannot hold: a sculpt, a lighting
    setup, the reference empties someone spent an hour placing. A PARAMS script
    rebuilds a shape; it does not rebuild an afternoon of sculpting.

    **It saves a COPY, and that is the whole design.** The artist's own file —
    the one they have been pressing Ctrl+S on — is not retargeted, not moved and
    not renamed; their next Ctrl+S goes exactly where it went before. The report
    hands back `session_file` so you can say that rather than promise it. Say it
    every time: it is what makes this safe to accept.

    - `project`: the folder name under `projects/`. Omit it and the add-on uses
      the project the PartForge panel is pointed at, which is the one the artist
      is visibly working on. The folder is created when it is not there yet, so
      a sculpt that never had a project gets one.

    **Offer this after substantial scene work** — a sculpt, a voxel remesh, a
    merge, references placed — and never after a cheap parametric rebuild, which
    `partforge_generate` reproduces from the script in seconds. Once it is saved,
    the project's card in the Library grows an **Open** button that loads the
    scene back, which is the point of saving at all: tell them that.

    Needs Blender running with the Forge add-on server started.
    """
    params: Dict[str, Any] = {}
    if project and project.strip():
        params["project"] = project.strip()

    result = blender_client.send_command(
        "save_project_blend",
        params,
        read_timeout=max(config.BLENDER_READ_TIMEOUT, _BLEND_TIMEOUT),
    )
    return fmt_project_save_report(result)


@app.tool()
def open_project_blend(name: str, confirm: bool = False) -> str:
    """Load `projects/<name>/<name>.blend` into Blender, replacing the scene.

    **Never call this unasked.** Opening a file throws the running scene away
    and Blender resets its undo stack on a file load, so there is no Ctrl+Z
    afterwards — this runs when the artist asked for this project's scene, and
    at no other time.

    - `name`: the project's folder name under `projects/`.
    - `confirm` (default false): permission to discard unsaved work. **Leave it
      false.** When there is anything to lose the add-on opens nothing and
      answers `needs_confirmation` with a sentence naming exactly what would go.
      That is a question for the artist, not a step for you: relay it, wait for
      a plain yes, and only then call again with `confirm=true`. Offer
      `save_project_blend` first — it keeps the current scene as a copy, and
      then the open costs nothing at all.

    A project with no scene file yet is refused with the sentence that makes
    one (Save Scene to Project); the artist's own route is the **Open** button
    on the project's Library card.

    The add-on's socket survives the file load — the pump is registered
    persistent — so you can carry straight on. Call `get_scene_info` before you
    act on anything: every object name you knew belongs to the previous file.

    Needs Blender running with the Forge add-on server started.
    """
    project = (name or "").strip()
    if not project:
        raise ForgeError(
            "Which project? open_project_blend takes the folder name under "
            'projects/, e.g. "small-magnet-holder".'
        )

    result = blender_client.send_command(
        "open_project_blend",
        {"name": project, "confirm": bool(confirm)},
        read_timeout=max(config.BLENDER_READ_TIMEOUT, _BLEND_TIMEOUT),
    )
    return fmt_project_open_report(result)


# ---------------------------------------------------------------------------
# Phase 7 — meshgen: a picture becomes a mesh (127.0.0.1:8902)
# ---------------------------------------------------------------------------

#: Voxel repair on a 200k-triangle mesh blocks Blender's main thread for a
#: while, so the import gets its own budget rather than the per-command default.
_IMPORT_TIMEOUT = 300.0


def _import_generated(
    mesh_path: str, name: str = ""
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """Bring the .glb into Blender, repaired. Returns (result, problems).

    Blender being down does NOT lose the run: minutes of GPU time produced a
    file, and the caller reports that path instead of an error.
    """
    params: Dict[str, Any] = {"path": mesh_path, "repair": True}
    if name:
        params["name"] = name
    try:
        result = blender_client.send_command(
            "import_generated",
            params,
            read_timeout=max(config.BLENDER_READ_TIMEOUT, _IMPORT_TIMEOUT),
        )
    except BackendUnavailable as exc:
        return None, [
            f"Blender is not running, so nothing was imported ({exc}). The .glb "
            "is written and safe — open Blender, start the Forge server, then "
            "import_generated(path=...) brings it in repaired."
        ]
    except ForgeError as exc:
        return None, [f"The mesh was generated but importing it failed: {exc}"]
    return result, []


def _check_generated(object_name: Optional[str]) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """The print verdict for what just landed, via the add-on's check_model."""
    if not object_name:
        return None, []
    try:
        return blender_client.send_command(
            "check_model",
            {"object": object_name},
            read_timeout=max(config.BLENDER_READ_TIMEOUT, config.SERVICE_CHECK_TIMEOUT),
        ), []
    except ForgeError as exc:
        return None, [
            f"The mesh is in the scene, but the print check did not run: {exc}. "
            "Run check_model yourself once that backend is up."
        ]


@app.tool()
def generate_3d(
    image_path: str,
    side_image: Optional[str] = None,
    back_image: Optional[str] = None,
    left_image: Optional[str] = None,
    backend: Optional[str] = None,
    wait: bool = True,
    project: Optional[str] = None,
) -> str:
    """Turn a PHOTO or SKETCH into a 3D mesh in the scene. Takes about 5 minutes.

    One call does the whole job: the picture goes to the meshgen service
    (127.0.0.1:8902), the AI model builds a textured mesh, the .glb is imported
    into Blender, voxel-REPAIRED on the way in, and print-checked — so what you
    report is an object the artist can see plus a verdict, not a file path.

    **Say the five minutes out loud BEFORE you start.** It is minutes of GPU
    work (measured: 304 s trellis2 / 249 s pixal3d on this machine), one job at
    a time. An artist who was not warned thinks it hung.

    **When to use this, and when NOT to.** Reach for meshgen when the shape is
    organic, stylised or a one-off and looking right matters more than measuring
    right: a creature, a character bust, an ornament, a base to sculpt on. Do
    NOT use it for anything functional, dimensioned or printable-to-fit — a
    bracket, a holder, a lid, anything that must be a named number of
    millimetres. That is partforge_new_part's job, and a generated mesh can
    never give you crisp faces or exact sizes.

    - `image_path`: an absolute .png/.jpg/.jpeg/.webp/.bmp on this machine. The
      object is named after the file, so `gecko.png` lands as `gecko` rather
      than the exporter's `Mesh_0`. This is always the FRONT view.
    - `side_image` / `back_image` / `left_image`: MORE VIEWS of the same
      object, same rules as image_path. Any of them switches the job to
      multi-view pixal3d (measured on this machine: ~11.1 GB peak at 4 views,
      82-106 s), which conditions the geometry on every view instead of
      guessing the unseen sides — offer it whenever the artist has a side or
      back reference, exactly the "front and side" ask. `side_image` is the
      object's RIGHT side; views must share framing and lighting. trellis2
      cannot take views and is refused up front.
    - `backend`: omit for the service's default (trellis2 single-view;
      pixal3d automatically when views are given). "pixal3d" is the other
      installed model; anything else is refused with the list.
    - `wait`: true (default) blocks until the mesh is in the scene. false hands
      back the job id immediately — then poll meshgen_status(job_id).
    - `project`: the part this mesh belongs to. Pass it whenever there is one
      and the .glb is written straight into `projects/<slug>/models/` instead of
      the service's scratch folder — so it appears on the Library tab's Models
      row badged with its project, and is still findable in a month. Omit it
      only for a genuinely loose experiment; the default stays the meshgen
      output folder. The name is slugged the same way partforge_new_part slugs
      one ("dog bowl holder" -> `dog-bowl-holder`), so pass the SAME name you
      used for the part and both halves land in one folder. Nothing is ever
      overwritten: a second generation for the same picture becomes `-2`.

    What comes back is honest about three things, and so must you be:

    - the mesh is voxel-repaired ALWAYS, because raw image-to-3D output is never
      manifold (paper-thin walls, boundary edges, inconsistent winding) and
      nothing downstream works until it is one closed shell;
    - the print verdict usually fails on min_wall. That is the correct diagnosis
      of a generated mesh, not a broken tool — report it plainly and say what
      thickening or scaling it needs;
    - nothing in a picture says how big the thing is. Scale is a decision the
      artist makes; ask for one real dimension.

    Next steps to offer: rigforge_retopo for a game asset (clean quads, then
    tags/UV/rig), check_model then repairs for printing, and sculpting for
    detail — never promise fine detail from the generator.
    """
    image = meshgen_image_path(image_path)
    chosen = (backend or "").strip() or None

    views: Dict[str, str] = {}
    for name, extra in (("side", side_image), ("back", back_image),
                        ("left", left_image)):
        if extra is not None and str(extra).strip():
            views[name] = str(meshgen_image_path(extra))
    options: Optional[Dict[str, Any]] = None
    if views:
        if chosen not in (None, "pixal3d"):
            raise BackendError(
                f"Extra views need the pixal3d backend; {chosen!r} is "
                "single-view only. Drop the views or drop the backend "
                "override."
            )
        chosen = "pixal3d"
        views["front"] = str(image)
        options = {"views": views}

    # Born filed, when there is a project to file it into. The path is resolved
    # BEFORE the job is submitted so a bad project name costs nothing — five
    # minutes of GPU work and then a refusal about a folder name would be the
    # worst possible order to discover it in.
    filed_to = ""
    output: Optional[str] = None
    if project is not None and str(project).strip():
        filed_to = project_slug(project)
        output = str(generated_output_path(filed_to, image))

    submitted = meshgen_client.generate3d(str(image), backend=chosen,
                                          options=options, output=output)
    job_id = str(submitted.get("job_id") or "").strip()
    if not job_id:
        raise BackendError(
            "meshgen accepted the job but returned no job_id, so there is "
            f"nothing to follow: {submitted}"
        )

    if not wait:
        return fmt_submitted_report(image, submitted, filed_to)

    job, stages = meshgen_client.wait_for_job(job_id)
    state = str(job.get("state") or "").lower()

    if state == "error":
        raise BackendError(
            f"meshgen could not make a model from {image.name}: "
            f"{job.get('error') or 'no reason given'}\n"
            "Nothing is in the scene. A photo with one clear subject on a plain "
            "background works best; if the service reports missing models, "
            "meshgen_status names the files and where they go."
        )
    if state != "done":
        return fmt_generate_report(image, submitted, job, stages, filed_to=filed_to)

    mesh_path = str(job.get("mesh_path") or "")
    if not mesh_path:
        return fmt_generate_report(
            image, submitted, job, stages,
            problems=["meshgen finished but named no file, so nothing could be imported."],
            filed_to=filed_to,
        )

    imported, problems = _import_generated(mesh_path, generated_object_name(image))
    check = None
    if imported:
        check, check_problems = _check_generated(imported.get("object"))
        problems.extend(check_problems)
    return fmt_generate_report(image, submitted, job, stages, imported, check,
                               problems, filed_to=filed_to)


@app.tool()
def meshgen_status(job_id: Optional[str] = None) -> str:
    """Is the picture-to-3D service up, and how far has a job got?

    Never fails. Call it when generate_3d(wait=false) gave you a job id, when a
    generation seems slow, or before promising an artist that a photo can become
    a model at all.

    Two things it reports that are easy to misread:

    - `progress` is the fraction through the CURRENT STAGE and it resets every
      time the pipeline moves to the next one. Tell the artist which stage it is
      on ("remeshing", "baking the texture"), never a percentage of the job.
    - a "models_missing" status is not a crash: the 18.5 GB of weights is a
      separate download, and the report names each missing file, where it should
      live and where to fetch it. Nothing is ever downloaded automatically.

    This service is optional. If it is down and the artist wants a functional,
    dimensioned part anyway, build it parametrically instead of waiting.
    """
    detail = ""
    try:
        payload = meshgen_client.health()
    except ForgeError as exc:
        payload = None
        detail = str(exc)

    job = None
    wanted = (job_id or "").strip()
    if wanted and payload is not None:
        try:
            job = meshgen_client.job(wanted)
        except ForgeError as exc:
            detail = f"{detail}\n  job {wanted}: {exc}".strip()
    report = fmt_meshgen_status(payload, job, detail)
    if wanted and payload is not None and job is None:
        report = f"{report}\n  (no job {wanted} — ids are forgotten when the service restarts)"
    return report


# ---------------------------------------------------------------------------
# RigForge (Phase 3) — tags, manifest, retopology, UV
# ---------------------------------------------------------------------------


def _face_target(
    faces: Optional[List[int]],
    use_selection: bool,
    *,
    allow_neither: bool = False,
    neither_hint: str = "",
) -> Dict[str, Any]:
    """Resolve the `faces` / `use_selection` pair into wire params.

    The contract writes these as alternatives (``"faces": [...] |
    "use_selection": true``) without saying what happens when both or neither
    arrive. Both is an explicit error rather than a silent precedence rule —
    the same call that guesses wrong here silently tags the wrong geometry, and
    partforge_* already prefers a readable refusal (apply_transforms with
    nothing to apply, a negative joint tolerance) over a quiet default.
    """
    if faces is not None and use_selection:
        raise ForgeError(
            "Pass either `faces` (explicit indices) or use_selection=true "
            "(Blender's current face selection), not both — they name different "
            "geometry and there is no safe way to guess which one you meant."
        )
    if faces is not None:
        return {"faces": normalize_face_indices(faces)}
    if use_selection:
        return {"use_selection": True}
    if allow_neither:
        return {}
    raise ForgeError(
        "No faces given. Pass `faces` as a list of face indices, or "
        "use_selection=true to use Blender's current face selection." + neither_hint
    )


@app.tool()
def rigforge_list_tags(object: Optional[str] = None) -> str:
    """List the RigForge body-part tags on a mesh, with their vertex/face counts.

    Tags are vertex groups (prefixed `tag_` in Blender) that name parts of a
    sculpt — "Head", "Arm.L", "Ear.R" — so retopology, UV seams and rigging know
    what is what. Call this before tagging to see what already exists, and after
    a retopo to confirm the tags transferred onto the new mesh.
    """
    result = blender_client.send_command("rigforge_list_tags", _target(object))
    return fmt_tag_table(result.get("tags"), f"'{object}'" if object else "the active object")


@app.tool()
def rigforge_tag(
    tag: str,
    faces: Optional[List[int]] = None,
    use_selection: bool = False,
    replace: bool = False,
    object: Optional[str] = None,
) -> str:
    """Tag faces of a mesh as a named body part ("Head", "Arm.L", "Ear.R").

    Pass EXACTLY ONE of:
    - `use_selection=true` — tag whatever faces are selected in Blender now.
    - `faces` — an explicit list of face indices.
    Passing both, or neither, is an error.

    For a request phrased by SHAPE or POSITION rather than by index — "the two
    lumps on top are the ears", "everything below the waist is legs" — you do
    not know the indices, so do it in two calls:

      1. execute_blender_python: enter Edit Mode on the object, deselect all,
         and select the faces by their centre position, e.g. select polygons
         whose centre z is above a threshold and |x| is off-centre for ears.
         (Selecting in Object Mode via `mesh.polygons[i].select` also works.)
      2. rigforge_tag(tag="Ear.L", use_selection=True).

    Check the result's face count against what you expected before moving on; if
    it looks wrong, adjust the selection and re-run with `replace=True`.

    `replace=False` (default) adds these faces to an existing tag of that name;
    `replace=True` makes the tag exactly this set. The `tag_` prefix is added by
    the add-on — pass the bare part name.
    """
    params = _target(object)
    params["tag"] = normalize_tag_name(tag)
    params.update(
        _face_target(
            faces,
            use_selection,
            neither_hint=(
                " For a positional request ('the lumps on top are ears'), select "
                "the faces with execute_blender_python first, then call this with "
                "use_selection=true."
            ),
        )
    )
    params["replace"] = bool(replace)

    result = blender_client.send_command("rigforge_tag", params)
    how = "the current selection" if use_selection else f"{len(params.get('faces', []))} face(s)"
    return ok(
        f"tagged {how} as '{result.get('tag', params['tag'])}' on "
        f"{object or 'the active object'}",
        f"{result.get('vertex_count', '?')} vertices in the tag"
        + (", replaced" if replace else ", added"),
    )


@app.tool()
def rigforge_untag(
    tag: str,
    faces: Optional[List[int]] = None,
    use_selection: bool = False,
    object: Optional[str] = None,
) -> str:
    """Remove faces from a RigForge tag, or delete the tag entirely.

    - Omit BOTH `faces` and `use_selection` to remove the whole tag (the vertex
      group goes away; the geometry is untouched).
    - Pass `faces` OR `use_selection=true` to remove just those faces from the
      tag and leave the rest of it alone. Passing both is an error.

    Nothing is deleted from the mesh either way — this only changes labelling.
    """
    params = _target(object)
    params["tag"] = normalize_tag_name(tag)
    scope = _face_target(faces, use_selection, allow_neither=True)
    params.update(scope)

    blender_client.send_command("rigforge_untag", params)
    if not scope:
        return ok(f"tag '{params['tag']}' removed from {object or 'the active object'}")
    how = "the current selection" if use_selection else f"{len(scope.get('faces', []))} face(s)"
    return ok(
        f"{how} removed from tag '{params['tag']}' on {object or 'the active object'}"
    )


@app.tool()
def rigforge_manifest(
    action: Literal["save", "load", "get"] = "get",
    path: Optional[str] = None,
    archetype: Optional[str] = None,
    motion_notes: Optional[str] = None,
    object: Optional[str] = None,
) -> str:
    """Read or write a character's `character.json` manifest (see templates/).

    The manifest is the durable record of what a sculpt is: its name, archetype
    (biped / quadruped / custom), the tag list mirrored from the mesh's vertex
    groups, retopology budgets, plain-language motion notes and the action list.

    - "get": return the manifest as it stands on the object. No file is touched.
    - "save": write it to disk (`path`, or the add-on's default beside the
      .blend). Missing folders are created and the extension is set to .json.
    - "load": read a manifest file and apply it to the object.

    `archetype` and `motion_notes` are forwarded to the add-on verbatim and take
    effect when the manifest is written — set them on a "save". `motion_notes`
    is plain language for the animator ("ears are floppy and lag behind the
    head", "hops rather than walks"), not a schema.

    `path` is only meaningful for save/load; passing it with "get" is an error
    rather than a silent no-op.
    """
    if path is not None and str(path).strip() and action == "get":
        raise ForgeError(
            "`path` is only meaningful for action 'save' or 'load'. 'get' returns "
            "the manifest already on the object — use action='load' to read that "
            "file, or action='save' to write to it."
        )

    resolved: Optional[Any] = None
    params = _target(object)
    params["action"] = action

    if path is not None and str(path).strip():
        resolved = resolve_path(path, label="manifest path")
        if resolved.suffix.lower() != ".json":
            resolved = resolved.with_suffix(".json")
        if action == "save":
            ensure_parent_dir(resolved)
        elif not resolved.is_file():
            raise ForgeError(
                f"No manifest at {resolved}. Point `path` at a character.json "
                "(see templates/character.json), or omit it to use the add-on's "
                "default location."
            )
        params["path"] = str(resolved)

    if archetype is not None and str(archetype).strip():
        params["archetype"] = str(archetype).strip()
    if motion_notes is not None:
        params["motion_notes"] = str(motion_notes)

    result = blender_client.send_command("rigforge_manifest", params)
    return fmt_manifest_report(
        action, f"'{object}'" if object else "the active object", result, resolved
    )


@app.tool()
def rigforge_retopo(
    platform: Literal["desktop", "mobile"] = "desktop",
    target_faces: Optional[int] = None,
    lods: int = 0,
    bake_normals: bool = False,
    bake_resolution: int = 2048,
    keep_original: bool = True,
    object: Optional[str] = None,
) -> str:
    """Turn a dense sculpt into a clean, animatable game mesh.

    One call runs the whole chain: voxel remesh to seal the sculpt, Quadriflow to
    the face budget, shrinkwrap back onto the original so the silhouette
    survives, and tag transfer by proximity so the body-part tags land on the new
    mesh. Optionally bakes a high-to-low normal map and builds decimated LODs.

    Face budget comes from `platform` unless `target_faces` overrides it:
    - "desktop": 15000 faces — the templates/character.json desktop budget.
    - "mobile":  5000 faces — for phone targets and crowds.

    - `lods`: extra decimated levels beyond the base mesh (0 = none, 2 is
      typical: half and quarter density).
    - `bake_normals`: bake the sculpt's detail into a normal map on the retopo
      mesh — this is what makes a 5k-face mesh still read as a sculpt.
      `bake_resolution` (px, only used when baking) is 2048 by default; 1024 for
      mobile, 4096 only when the character fills the screen.
    - `keep_original`: True (default) leaves the sculpt in the scene as the bake
      source and as an undo of last resort.

    Slow on a dense sculpt — Blender blocks while Quadriflow runs. Reports every
    object created with its face count; run rigforge_list_tags afterwards to
    confirm the tags came across, then rigforge_auto_uv to unwrap.
    """
    if target_faces is not None:
        target = int(target_faces)
        if target < 100:
            raise ForgeError(
                f"target_faces must be at least 100; got {target}. Omit it to use "
                f"the {platform} preset ({PLATFORM_TARGET_FACES[platform]} faces)."
            )
        source = "explicit target"
    else:
        target = PLATFORM_TARGET_FACES[platform]
        source = f"{platform} preset"

    if not 0 <= int(lods) <= 8:
        raise ForgeError(f"lods must be between 0 and 8; got {lods}.")
    if bake_normals and not 64 <= int(bake_resolution) <= 8192:
        raise ForgeError(
            f"bake_resolution must be between 64 and 8192 px; got {bake_resolution}."
        )

    params = _target(object)
    params.update(
        {
            "target_faces": target,
            "platform": platform,
            "lods": int(lods),
            "bake_normals": bool(bake_normals),
            "keep_original": bool(keep_original),
        }
    )
    if bake_normals:
        params["bake_resolution"] = int(bake_resolution)

    result = blender_client.send_command("rigforge_retopo", params)
    summary = f"{target} face target ({source}), {int(lods)} extra LOD(s)"
    if bake_normals:
        summary += f", normals baked at {int(bake_resolution)} px"
    if not keep_original:
        summary += ", original removed"
    return fmt_retopo_report(f"'{object}'" if object else "the active object", result, summary)


@app.tool()
def rigforge_auto_uv(
    seams_from_tags: bool = True,
    margin: float = 0.003,
    angle_limit: float = 66.0,
    object: Optional[str] = None,
) -> str:
    """Unwrap a mesh into a packed UV atlas, cutting seams at the tag boundaries.

    Run this on the RETOPO mesh, after rigforge_retopo — unwrapping a raw sculpt
    wastes the atlas on geometry that is about to be replaced.

    - `seams_from_tags` (default True): mark seams where one body-part tag meets
      another — neck, shoulders, wrists — so islands follow anatomy and stretch
      lands in places nobody looks. Set False to let the angle limit decide
      alone, for a mesh with no tags.
    - `angle_limit`: degrees of face-angle change that forces a new island (66 is
      Blender's own default; lower = more, flatter islands).
    - `margin`: gap between islands in UV space (0..1). 0.003 keeps mipmaps from
      bleeding one island into the next; raise it for low-resolution textures.

    Reports the island count and how much of the UV square the layout uses —
    low coverage means wasted texture resolution, so consider fewer seams.
    """
    if not 0.0 <= float(margin) < 0.5:
        raise ForgeError(f"margin must be between 0 and 0.5 (UV space); got {margin}.")
    if not 0.0 < float(angle_limit) < 90.0:
        raise ForgeError(f"angle_limit must be between 0 and 90 degrees; got {angle_limit}.")

    params = _target(object)
    params.update(
        {
            "seams_from_tags": bool(seams_from_tags),
            "margin": float(margin),
            "angle_limit": float(angle_limit),
        }
    )
    result = blender_client.send_command("rigforge_auto_uv", params)
    summary = (
        ("seams at tag boundaries" if seams_from_tags else "no tag seams")
        + f", angle limit {fmt_number(angle_limit)} deg, margin {fmt_number(margin, 4)}"
    )
    return fmt_uv_report(f"'{object}'" if object else "the active object", result, summary)


# ---------------------------------------------------------------------------
# RigForge (Phase 4) — metarig, rig generation, weights, Godot export
# ---------------------------------------------------------------------------


@app.tool()
def rigforge_metarig(
    archetype: Literal["auto", "biped", "quadruped", "custom"] = "auto",
    modules: Optional[List[Dict[str, Any]]] = None,
    object: Optional[str] = None,
    preset: Optional[str] = None,
    joints_file: Optional[str] = None,
    joints_weight: Optional[float] = None,
    joints_tolerance: Optional[float] = None,
    joints_disagree_band: Optional[float] = None,
    joints_axis_up: Optional[Literal["X", "Y", "Z"]] = None,
) -> str:
    """Place and scale a metarig on a tagged mesh, fitted to its body-part tags.

    Step four of the RigForge pipeline: rigforge_tag -> rigforge_retopo ->
    rigforge_auto_uv -> **rigforge_metarig** -> rigforge_generate_rig ->
    (animate) -> rigforge_export_godot. Run it on the RETOPO mesh, once the tags
    are on it — the bones are placed from tag geometry (head top, chin, shoulder,
    elbow, wrist, hip, knee, ankle), so an untagged or half-tagged mesh gives a
    metarig in the wrong place.

    - `archetype` "auto" (default) reads the character.json manifest and falls
      back to guessing from the tags; "biped" / "quadruped" force a skeleton;
      "custom" builds only from `modules`.
    - `modules`: extra chains to build, passed to the add-on verbatim, e.g.
      [{"kind": "tail", "tag": "Tail"}, {"kind": "chain", "tag": "Ear.L"}].
      Omit it to let the archetype decide. Ear/tail chains are flagged for
      spring/jiggle from the manifest's motion_notes by the add-on — there is no
      parameter for that here, write the notes with rigforge_manifest instead.
    - `preset`: which Rigify template to start from; omit (the default) and the
      add-on picks — a 29-bone basic human unless face or hand tags exist.

    **Predicted joints (`joints_file`) are a THIRD source, and the weakest one.**
    A neural joint detector's output, written as JSON by a runner outside
    Blender, refines the tag fit — it never replaces it. Measured on our own test
    biped, UniRig put the spine within 8-36 mm and the shoulders 253-266 mm out,
    so the blend is asymmetric on purpose: tags anchor, predictions pull a
    landmark part of the way in, and a prediction that disagrees is REPORTED and
    ignored. Read the `joints` block's disagreements before you trust a fit.

    - `joints_file`: the detector's JSON (`{"schema": "forge.joints/1", ...}`).
      Omit it and this command behaves exactly as it always did.
    - `joints_weight`: 0-1, default 0.5. How far a landmark moves toward a
      prediction it agrees with. 0 reports everything and moves nothing; 1 hands
      the landmark over outright.
    - `joints_tolerance`: 0-1, default 0.12 — "agrees with" as a fraction of the
      mesh's largest dimension.
    - `joints_disagree_band`: 1-20, default 3 — how far past tolerance still
      counts as a second opinion worth reporting rather than noise.
    - `joints_axis_up`: "X"/"Y"/"Z", overriding the file's own declared frame.
      Only for a producer that lies about its axes: the file is refused outright
      when fewer than half its joints land inside the mesh, and the warning names
      the reading that WOULD have worked.

    Reports the metarig's name, its bone count and which bones each tag drove,
    and relays the add-on's warnings (a missing landmark tag shows up here, not
    later as a bone in the wrong place). Nothing is skinned yet — look at the
    placement in Blender before running rigforge_generate_rig.
    """
    params = _target(object)
    params["archetype"] = archetype
    if modules is not None:
        params["modules"] = normalize_modules(modules)
    if preset is not None and str(preset).strip():
        params["preset"] = str(preset).strip()

    hints = ""
    if joints_file is not None and str(joints_file).strip():
        # Resolved against the project workspace the way every other file-taking
        # tool does, and it must exist: a typo'd path would otherwise become a
        # silent tag-only fit that looks exactly like a successful refinement.
        joints = resolve_path(joints_file, must_exist=True, label="joints file")
        params["joints_file"] = str(joints)
        hints = f", refined by {joints.name}"
    elif any(value is not None for value in (joints_weight, joints_tolerance,
                                             joints_disagree_band, joints_axis_up)):
        raise ForgeError(
            "joints_weight / joints_tolerance / joints_disagree_band / "
            "joints_axis_up only mean anything with a `joints_file` — they tune "
            "how far a predicted joint is allowed to move a landmark, and there "
            "are no predictions without the file."
        )

    for name, value, low, high in (
        ("joints_weight", joints_weight, 0.0, 1.0),
        ("joints_tolerance", joints_tolerance, 0.0, 1.0),
        ("joints_disagree_band", joints_disagree_band, 1.0, 20.0),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ForgeError(f"{name} must be a number (got {value!r}).")
        if not low <= float(value) <= high:
            raise ForgeError(
                f"{name} must be between {fmt_number(low, 2)} and "
                f"{fmt_number(high, 2)} (got {fmt_number(value, 3)})."
            )
        params[name] = float(value)
    if joints_axis_up is not None and str(joints_axis_up).strip():
        params["joints_axis_up"] = str(joints_axis_up).strip().upper()

    result = blender_client.send_command("rigforge_metarig", params)
    summary = f"archetype {archetype}"
    if "preset" in params:
        summary += f", preset {params['preset']}"
    if modules is not None:
        summary += f", {len(params['modules'])} extra module(s)"
    summary += hints
    return fmt_metarig_report(
        f"'{object}'" if object else "the active object", result, summary
    )


@app.tool()
def rigforge_generate_rig(
    metarig: Optional[str] = None,
    mesh: Optional[str] = None,
    parent_with_weights: bool = True,
    cleanup: bool = True,
) -> str:
    """Generate the real rig from a metarig and skin the mesh to it.

    Step five: Rigify generate, then parent the mesh with automatic weights, then
    per-tag cleanup rules (no head weights below the neck tag, and so on) and a
    normalize pass. Run rigforge_metarig first and check the bone placement — the
    weights are only as good as the skeleton they came from.

    - `metarig`: the metarig object; omit to use the one the add-on just placed
      (or the active object).
    - `mesh`: the mesh to skin; omit to use the metarig's tagged mesh.
    - `parent_with_weights`: False generates the control rig but skins nothing,
      for when the mesh is not final yet.
    - `cleanup`: False keeps Blender's raw automatic weights — usually worse, but
      useful when comparing against a hand-painted result.

    Reports the rig, what got skinned and the cleanup counts, with the add-on's
    warnings first. Inspect the result with rigforge_weights(action="report"),
    animate, then rigforge_export_godot.
    """
    params: Dict[str, Any] = {
        "parent_with_weights": bool(parent_with_weights),
        "cleanup": bool(cleanup),
    }
    if metarig and metarig.strip():
        params["metarig"] = metarig.strip()
    if mesh and mesh.strip():
        params["mesh"] = mesh.strip()

    result = blender_client.send_command("rigforge_generate_rig", params)
    summary = (
        ("parented with automatic weights" if parent_with_weights else "no skinning")
        + (", per-tag cleanup + normalize" if cleanup else ", raw weights kept")
    )
    return fmt_rig_report(result, summary)


@app.tool()
def rigforge_weights(
    action: Literal["report", "cleanup", "normalize"] = "report",
    max_influences: Optional[int] = None,
    object: Optional[str] = None,
) -> str:
    """Inspect or repair a skinned mesh's vertex weights.

    Sits between rigforge_generate_rig and rigforge_export_godot, and is the
    thing to run when a deformation looks wrong.

    - "report" (default): read the skinning and say what is off — vertices over
      the influence limit, unnormalized or unweighted vertices, weights that
      cross a tag boundary. Changes nothing.
    - "cleanup": drop the smallest influences until every vertex is within the
      limit, remove near-zero weights and weights the tag rules forbid.
    - "normalize": make every vertex's weights sum to 1 without changing which
      bones influence it.

    `max_influences` is the bones-per-vertex limit — Godot's own limits are 4
    (the default the add-on applies) and 8; more than 8 will be cut by the engine
    regardless. It applies to "report" too, as the limit that report measures
    against. Omit it to use the add-on's default.
    """
    if max_influences is not None:
        limit = int(max_influences)
        if not 1 <= limit <= 8:
            raise ForgeError(
                f"max_influences must be between 1 and 8; got {limit}. Godot "
                "supports 4 or 8 bones per vertex, so anything above 8 is cut by "
                "the engine anyway."
            )

    params = _target(object)
    params["action"] = action
    if max_influences is not None:
        params["max_influences"] = int(max_influences)

    result = blender_client.send_command("rigforge_weights", params)
    return fmt_weights_report(
        action, f"'{object}'" if object else "the active object", result
    )


@app.tool()
def rigforge_export_godot(
    path: str,
    rig: Optional[str] = None,
    meshes: Optional[List[str]] = None,
    actions: Union[str, List[str]] = "all",
    root_motion: bool = False,
    deform_only: bool = True,
    godot_import_script: bool = True,
) -> str:
    """Bake the animation onto the deform bones and write a Godot-ready glTF.

    The last step: rigforge_tag -> rigforge_retopo -> rigforge_auto_uv ->
    rigforge_metarig -> rigforge_generate_rig -> (animate) ->
    **rigforge_export_godot**. Actions are baked from the control rig onto the
    deform bones, the control bones are stripped, and the result is exported as
    glTF with Godot's conventions (Y-up, applied transforms, unit scale, `-col`
    and `-lod` name suffixes, `-loop` actions).

    - `path`: where the glTF goes. `.glb` (default when the suffix is anything
      else) is the single-file form Godot prefers; `.gltf` is honoured when asked
      for. Missing folders are created.
    - `rig`: the rig object; omit to use the active/only one.
    - `meshes`: which meshes to include; omit to take everything skinned to the
      rig. Name the LOD meshes here too if they should ship.
    - `actions`: "all" (default), or the names to export — a list, or a
      comma-separated string.
    - `root_motion`: True keeps the root bone's motion in the animation for the
      engine to drive the character with; False (default) bakes in place.
    - `deform_only`: True (default) strips control bones — turn it off only to
      debug the rig, never for a shipping export.
    - `godot_import_script`: True (default) also writes a `.gd` import helper /
      `.import` settings next to the glTF.

    Read the report's WARNINGS block before importing: a missing action, an
    unweighted mesh or a stripped bone that something still referenced shows up
    there, and all of them are cheaper to fix in Blender than in Godot.
    """
    out = resolve_path(path, label="export path")
    if out.suffix.lower() not in GLTF_SUFFIXES:
        out = out.with_suffix(".glb")
    ensure_parent_dir(out)

    wanted = normalize_actions(actions)
    params: Dict[str, Any] = {
        "path": str(out),
        "actions": wanted,
        "root_motion": bool(root_motion),
        "deform_only": bool(deform_only),
        "godot_import_script": bool(godot_import_script),
    }
    if rig and rig.strip():
        params["rig"] = rig.strip()
    names = [m.strip() for m in (meshes or []) if m and m.strip()]
    if names:
        params["meshes"] = names

    result = blender_client.send_command("rigforge_export_godot", params)
    summary = (
        ("every action" if wanted == "all" else f"{len(wanted)} action(s)")
        + (", root motion" if root_motion else ", baked in place")
        + (", deform bones only" if deform_only else ", CONTROL BONES KEPT")
        + (", + Godot import helper" if godot_import_script else "")
    )
    subject = f"'{rig.strip()}'" if rig and rig.strip() else "the rig"
    return fmt_export_report(subject, result, summary, str(out))


@app.tool()
def rig_check(
    rig: Optional[str] = None,
    mesh: Optional[str] = None,
    poses: Union[str, List[Any]] = "extreme",
    joints: Optional[List[str]] = None,
    max_poses: Optional[int] = None,
    intersections: bool = True,
) -> str:
    """Does the rig DEFORM? Pose every joint to its extremes and measure.

    Every other rigging tool stops at "the rig generated". This answers the
    question the artist asks next, with numbers rather than a screenshot: for
    each limb, spine and neck joint it poses the control to its extremes and
    measures three failures on the evaluated mesh — **volume loss** (the
    collapsed elbow), **new self-intersections** (the arm through the ribs) and
    **candy-wrapper twist** (the forearm pinched to a thread).

    Run it after rigforge_generate_rig and before rigforge_export_godot. A rig
    that exports and then breaks in the engine broke here first.

    - `poses`: "extreme" (default, 3 per joint: mid-flex, max-flex, max-twist —
      where the failures live), "quick" (1), "full" (7), or an explicit list:
      `[90, 140]` or `[{"label": "reach", "flex_deg": 95, "twist_deg": 30}]`.
    - `joints`: names to measure; omit for every deform-relevant joint found.
    - `max_poses`: 1-64, the per-joint ceiling. Omit for the add-on's default.
    - `intersections`: False skips the BVH overlap scan — the expensive part —
      when you only want volume and twist.

    **The thresholds are heuristics and the report says so.** They are the points
    at which each artefact becomes visible, not values calibrated against what
    artists accept, so a `fail` is a band you can argue with and NOT a fact about
    their sculpt. Quote the measurement next to the band that judged it. The pose
    is always restored — every bone's matrix, rotation mode and IK/FK value comes
    back, finished or interrupted.
    """
    params: Dict[str, Any] = {"intersections": bool(intersections)}
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if mesh and mesh.strip():
        params["mesh"] = mesh.strip()

    if isinstance(poses, (list, tuple)):
        if not poses:
            raise ForgeError(
                "`poses` was an empty list. Give it angles ([90, 140]), pose "
                'objects ([{"label": "reach", "flex_deg": 95}]), or one of '
                '"extreme" / "quick" / "full".'
            )
        params["poses"] = list(poses)
        described = f"{len(poses)} explicit pose(s)"
    else:
        wanted = str(poses or "extreme").strip().lower()
        if wanted not in ("extreme", "quick", "full"):
            raise ForgeError(
                f"Unknown pose set {poses!r}. Use \"extreme\" (default, the "
                'three where failures live), "quick" (one), "full" (seven), or '
                "a list of angles."
            )
        params["poses"] = wanted
        described = f"{wanted} poses"

    names = [str(name).strip() for name in (joints or []) if str(name).strip()]
    if names:
        params["joints"] = names
    if max_poses is not None:
        if isinstance(max_poses, bool) or not isinstance(max_poses, int):
            raise ForgeError(f"max_poses must be a whole number (got {max_poses!r}).")
        if not 1 <= max_poses <= 64:
            raise ForgeError(
                f"max_poses must be between 1 and 64 (got {max_poses}). It is a "
                "ceiling on poses per joint, not a target."
            )
        params["max_poses"] = int(max_poses)

    result = blender_client.send_command(
        "rig_check", params, read_timeout=config.PREVIEW_TIMEOUT
    )
    summary = (
        described
        + (f", {len(names)} named joint(s)" if names else ", every joint found")
        + ("" if intersections else ", intersection scan OFF")
    )
    return fmt_rig_check_report(result, summary)


_CORRECTIVES_AUTHOR_ONLY = ("angle_samples", "strength", "smooth", "weight_floor", "verify")


def _fmt_correctives_report(result: Mapping[str, Any], summary: str) -> str:
    """Whichever of author/report/clear ran, in that command's own shape."""
    mesh = result.get("mesh") or "(unnamed)"
    action = str(result.get("action") or "author")
    says = str(result.get("says") or "").strip()

    if action == "report":
        entries = [e for e in (result.get("correctives") or []) if isinstance(e, Mapping)]
        lines = [f"Correctives on '{mesh}' — {summary}"]
        if says:
            lines.append(f"  {says}")
        for entry in entries:
            bones = entry.get("driver_bones") or []
            driven = (
                f"driven by {', '.join(bones)}" if entry.get("driven") else "NOT driven"
            )
            lines.append(
                f"  {entry.get('name', '?')}: value={fmt_number(entry.get('value'), 3)}"
                f" ({'muted' if entry.get('muted') else 'active'}, {driven})"
            )
        return "\n".join(lines)

    if action == "clear":
        lines = [f"Correctives cleared on '{mesh}' — {summary}"]
        if says:
            lines.append(f"  {says}")
        lines.append("  " + fmt_counted("removed", result.get("removed")))
        lines.append("  " + fmt_counted("remaining", result.get("remaining")))
        return "\n".join(lines)

    # action == "author"
    lines = [f"Correctives authored on '{mesh}' — {summary}"]
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))
    before, after = result.get("gate_before"), result.get("gate_after")
    if before or after:
        lines.append(
            f"  gate: {str(before).upper() if before else '?'} -> "
            + (str(after).upper() if after else "(not re-verified)")
        )
    for row in (result.get("table") or [])[:8]:
        if not isinstance(row, Mapping):
            continue
        before_pct, after_pct = row.get("volume_loss_before_pct"), row.get("volume_loss_after_pct")
        flex_deg = row.get("flex_deg")
        lines.append(
            "  {label:<14} {flex:>4} deg  {before:>7} -> {after:<7} volume loss".format(
                label=str(row.get("label") or row.get("joint") or "?")[:14],
                # fmt_number at 0 places strips a whole float's own trailing
                # zeros (90.0 -> "9"), so round to an int first rather than
                # through the float formatter.
                flex=("?" if flex_deg is None else str(int(round(float(flex_deg))))),
                before=("-" if before_pct is None else f"{fmt_number(before_pct, 1)}%"),
                after=("-" if after_pct is None else f"{fmt_number(after_pct, 1)}%"),
            )
        )
    lines.append("  " + fmt_counted("shape keys written", result.get("shape_keys")))
    return "\n".join(lines)


@app.tool()
def rigforge_correctives(
    action: Literal["author", "report", "clear"] = "author",
    rig: Optional[str] = None,
    mesh: Optional[str] = None,
    joints: Optional[List[str]] = None,
    angle_samples: Optional[List[Any]] = None,
    strength: Optional[float] = None,
    smooth: Optional[float] = None,
    weight_floor: Optional[float] = None,
    verify: Optional[bool] = None,
) -> str:
    """Author bend-angle-driven corrective shape keys (JCMs), and measure the fix.

    THIS is the fix for `rig_check`'s volume-loss failures, and it comes before
    a re-weight: linear-blend skinning averages rigid transforms, and the
    average of two rotations is shorter than either one, so a joint thins as it
    bends no matter how good the weight paint is. A corrective shape key driven
    by the joint's own bend angle restores the lost volume, at zero when the
    limb is straight and full strength at the sampled angle — the same JCM every
    production pipeline uses. It works whether the limb is posed in FK or
    solved in IK.

    `action`:
    - "author" (default): measure the collapse, write the correctives, drive
      each one off its joint's bend angle, then re-measure with the harness and
      hand back the before/after volume-loss table. Always quote both numbers,
      never just the after — the harness's volume is a convex-hull measurement,
      so folding a limb moves it a little for reasons that are geometry, not
      skinning, and the before/after pair from the same instrument is the only
      reason the comparison means anything.
    - "report": what correctives this mesh already carries, driven or not.
    - "clear": remove the correctives (and their drivers) this tool wrote.
      `joints` narrows it; omit for all of them.

    `joints` (author/clear only): e.g. `["knee.L"]`. Omit on "author" and the
    add-on corrects whatever `rig_check` itself measures as failing on volume at
    full flex — it picks its own worst rather than needing a guess.

    Author-only tuning, all optional:
    - `angle_samples`: bend angles to correct at. A plain number is a FRACTION
      of that joint's own extreme (`[0.5, 1.0]` on a 140-degree knee samples 70
      and 140 degrees); `{"flex_deg": 90}` is an exact angle. Omit for the
      add-on's own defaults.
    - `strength` (0-1): how much of the measured collapse to restore.
    - `smooth` (0-1): how far the correction blends into the surrounding mesh.
    - `weight_floor` (0-1): the minimum bone weight for a vertex to be considered
      part of the joint's neighbourhood.
    - `verify`: True (default) re-runs the harness after authoring for the
      before/after table; False skips it and the report has no after column.

    There is no `direction` parameter — the restoring push is always radial,
    outward from the limb's own posed skeleton. Pushing along the posed surface
    normal was tried and measured worse (a knee's 29.7% loss became 39.4%,
    against 13.6% radial), because in the crease of a fold the two facing walls'
    normals point at each other; measurement and restoration have to share an
    axis.

    Not a substitute for a fair weight paint, and not a substitute for more
    geometry either — a blind density pass around a real knee moved the
    measurement 50.3% to 51.1%, inside the noise.
    """
    given_extra = {
        "joints": joints,
        "angle_samples": angle_samples,
        "strength": strength,
        "smooth": smooth,
        "weight_floor": weight_floor,
        "verify": verify,
    }
    if action == "report":
        offending = [name for name, value in given_extra.items() if value is not None]
        if offending:
            raise ForgeError(
                f"{', '.join(sorted(offending))} mean nothing to action='report': it "
                "reads back whatever the mesh already carries, nothing more."
            )
    elif action == "clear":
        offending = [
            name for name in _CORRECTIVES_AUTHOR_ONLY if given_extra[name] is not None
        ]
        if offending:
            raise ForgeError(
                f"{', '.join(sorted(offending))} mean nothing to action='clear': pass "
                "`joints` to narrow which correctives are removed, nothing else."
            )

    params: Dict[str, Any] = {"action": action}
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if mesh and mesh.strip():
        params["mesh"] = mesh.strip()

    names = [str(name).strip() for name in (joints or []) if str(name).strip()]
    if names:
        params["joints"] = names

    if action == "author":
        if angle_samples is not None:
            if not isinstance(angle_samples, (list, tuple)) or not angle_samples:
                raise ForgeError(
                    "`angle_samples` must be a non-empty list of fractions of the "
                    'joint\'s own extreme, 0 (exclusive) to 1, like [0.5, 1.0], or '
                    '{"flex_deg": 90} objects for an exact angle.'
                )
            params["angle_samples"] = list(angle_samples)
        for label, value in (
            ("strength", strength), ("smooth", smooth), ("weight_floor", weight_floor)
        ):
            if value is not None:
                params[label] = _walk_number(label, value, 0.0, 1.0)
        if verify is not None:
            params["verify"] = bool(verify)

    result = blender_client.send_command(
        "rigforge_correctives", params, read_timeout=config.PREVIEW_TIMEOUT
    )

    if action == "author":
        summary = (
            (f"{len(names)} named joint(s)" if names else "the harness's own worst joints")
            + (f", strength {fmt_number(strength, 2)}" if strength is not None else "")
            + (", not re-verified" if verify is False else "")
        )
    elif action == "clear":
        summary = f"{len(names)} named joint(s)" if names else "every corrective"
    else:
        summary = "current state"
    return _fmt_correctives_report(result, summary)


# ---------------------------------------------------------------------------
# RigForge (Phase 5) — cloth and animation
# ---------------------------------------------------------------------------


def _garment_source(tags: Optional[List[str]], use_selection: bool) -> Dict[str, Any]:
    """Resolve the `tags` / `use_selection` pair, on rigforge_tag's rules.

    The contract writes them as alternatives (``"tags": [names] |
    "use_selection"``) and says nothing about both or neither, so this behaves
    exactly like :func:`_face_target`: both is a refusal, because a garment cut
    from the wrong faces is a silent wrong answer.
    """
    if tags is not None and use_selection:
        raise ForgeError(
            "Pass either `tags` (the body-part tags the garment covers) or "
            "use_selection=true (Blender's current face selection), not both — "
            "they name different geometry and there is no safe way to guess "
            "which one you meant."
        )
    if tags is not None:
        return {"tags": normalize_tag_list(tags)}
    if use_selection:
        return {"use_selection": True}
    raise ForgeError(
        "No garment area given. Pass `tags` — the body parts the garment covers, "
        'e.g. ["Torso", "Arm.L", "Arm.R"]; rigforge_list_tags shows what the mesh '
        "has — or use_selection=true to use Blender's current face selection."
    )


@app.tool()
def rigforge_cloth(
    tags: Optional[List[str]] = None,
    use_selection: bool = False,
    name: Optional[str] = None,
    preset: Literal["cotton", "leather", "heavy"] = "cotton",
    output: Literal["skin_tight", "shapekeys", "bones"] = "skin_tight",
    offset_mm: Optional[float] = None,
    thickness_mm: Optional[float] = None,
    frames: Optional[int] = None,
    collision: bool = True,
    object: Optional[str] = None,
) -> str:
    """Grow a garment off a body mesh: duplicate the tagged area, offset, simulate.

    Step six, and the optional one: rigforge_tag -> rigforge_retopo ->
    rigforge_auto_uv -> rigforge_metarig -> rigforge_generate_rig ->
    **rigforge_cloth** -> rigforge_action / rigforge_keyframe / rigforge_retarget
    -> rigforge_export_godot. Run it on the RETOPO mesh (the one that is skinned),
    because the garment inherits that mesh's weights and density.

    Pass EXACTLY ONE of:
    - `tags` — the body parts the garment covers, e.g. ["Torso", "Arm.L", "Arm.R"].
    - `use_selection=true` — whatever faces are selected in Blender now.
    Passing both, or neither, is an error.

    `output` decides how the garment MOVES, and it is the choice that matters:
    - "skin_tight" (default): copies the body's weights, no simulation. A tunic
      that moves exactly with the character. Cheap, deterministic, exports
      perfectly, and right for most game characters.
    - "shapekeys": runs the cloth sim and bakes the settled result into shape
      key(s) — the drape a skin-tight copy cannot give you, still with no runtime
      cost. `frames` is how long the sim settles for.
    - "bones": v1-DEGRADED. The add-on may warn and fall back to another output;
      read the report's WARNINGS block before assuming a bone chain exists. Ask
      for it only when the user specifically wants engine-driven cloth bones.

    - `preset`: "cotton" (light, many folds), "leather" (stiff, heavy folds),
      "heavy" (cloak/curtain weight).
    - `offset_mm` / `thickness_mm`: how far the garment floats off the skin and
      how thick the shell is. Omit both to use the add-on's own defaults.
    - `frames`: simulation length. Only meaningful when a sim runs, so passing it
      with "skin_tight" is an error rather than a silent no-op.
    - `collision`: True (default) makes the body collide with the cloth. Turning
      it off is faster and always wrong-looking.
    - `name`: the garment object's name; omit and the add-on names it after the
      body and the tags.
    """
    params = _target(object)
    params.update(_garment_source(tags, use_selection))

    if frames is not None and output == "skin_tight":
        raise ForgeError(
            "`frames` is only meaningful when a simulation runs (output "
            "'shapekeys' or 'bones'). 'skin_tight' copies the body's weights and "
            "never simulates — drop `frames`, or ask for output='shapekeys'."
        )
    for label, value in (("offset_mm", offset_mm), ("thickness_mm", thickness_mm)):
        if value is not None and not 0.0 <= float(value) <= 100.0:
            raise ForgeError(
                f"{label} must be between 0 and 100 mm; got {value}. These are "
                "millimetres on the character's own scale, not scene units."
            )
    if frames is not None and not 1 <= int(frames) <= 1000:
        raise ForgeError(
            f"frames must be between 1 and 1000; got {frames}. A garment settles "
            "in 30-120 frames — more than that is usually a stuck simulation."
        )

    params["preset"] = preset
    params["output"] = output
    params["collision"] = bool(collision)
    if name is not None and str(name).strip():
        params["name"] = str(name).strip()
    if offset_mm is not None:
        params["offset_mm"] = float(offset_mm)
    if thickness_mm is not None:
        params["thickness_mm"] = float(thickness_mm)
    if frames is not None:
        params["frames"] = int(frames)

    result = blender_client.send_command("rigforge_cloth", params)

    covers = (
        "the current selection"
        if use_selection
        else ", ".join(params.get("tags", [])) or "?"
    )
    summary = f"{preset}, {output}, covering {covers}"
    if output == "bones":
        summary += " (bones output is v1-degraded — check the warnings)"
    if not collision:
        summary += ", no body collision"
    return fmt_cloth_report(
        f"'{object}'" if object else "the active object", result, summary
    )


#: Which `rigforge_action` arguments each action actually uses. The contract
#: lists `name`/`source` once for all six, so the meaning of each is pinned here
#: rather than guessed inside Blender: a `source` on a "delete" is a typo worth
#: refusing, not a parameter to drop on the floor.
_ACTION_NEEDS_NAME = {"new", "delete", "rename"}
_ACTION_NEEDS_SOURCE = {"duplicate", "rename"}
_ACTION_TAKES_NAME = {"new", "delete", "rename", "duplicate", "push_nla"}
_ACTION_TAKES_SOURCE = {"duplicate", "rename"}
_ACTION_TAKES_LOOP = {"new", "duplicate", "rename", "push_nla"}

_ACTION_ROLES = {
    "new": "`name` is the new action's name",
    "delete": "`name` is the action to delete",
    "duplicate": "`source` is the action to copy, `name` the copy's name (optional)",
    "rename": "`source` is the current name, `name` the new one",
    "push_nla": "`name` is the action to push (omit for the rig's current one)",
    "list": "'list' takes neither `name` nor `source` — it reports the whole library",
}


@app.tool()
def rigforge_action(
    action: Literal["new", "list", "delete", "duplicate", "rename", "push_nla"] = "list",
    name: Optional[str] = None,
    source: Optional[str] = None,
    rig: Optional[str] = None,
    loop: bool = False,
) -> str:
    """Manage the Godot action library on a rig: the clips, not their contents.

    Actions are the animation clips that ship — `idle`, `walk`, `run`, `jump`,
    `attack`. This tool creates, lists, deletes, copies, renames and stacks them;
    rigforge_keyframe puts motion INSIDE one, and rigforge_retarget imports one
    from a user-supplied capture file.

    Which arguments each `action` uses:
    - "list" (default): the whole library, with a loop badge and frame range per
      action. Takes neither `name` nor `source`.
    - "new": `name` is the clip to create.
    - "delete": `name` is the clip to remove.
    - "duplicate": `source` is the clip to copy; `name` names the copy (omit and
      the add-on picks one).
    - "rename": `source` is the current name, `name` the new one.
    - "push_nla": stash the action onto an NLA track so the next one starts
      clean — Blender only holds one active action at a time. `name` picks which,
      omit it for the rig's current action.

    `loop=true` marks a looping clip, and the add-on enforces the `-loop` name
    suffix that Godot's importer reads (`idle-loop`, `walk-loop`); the name is
    sent as typed and the report shows whatever the add-on settled on. Loop the
    cycles (idle/walk/run), never the one-shots (jump/attack).

    `rig` names the armature; omit it for the active/only one.
    """
    if name is not None and str(name).strip() and action not in _ACTION_TAKES_NAME:
        raise ForgeError(
            f"`name` means nothing to action '{action}': {_ACTION_ROLES[action]}."
        )
    if source is not None and str(source).strip() and action not in _ACTION_TAKES_SOURCE:
        raise ForgeError(
            f"`source` means nothing to action '{action}': {_ACTION_ROLES[action]}. "
            "`source` is only the action being copied (duplicate) or renamed."
        )

    params: Dict[str, Any] = {"action": action}
    if action in _ACTION_NEEDS_NAME and not (name and str(name).strip()):
        raise ForgeError(
            f"action '{action}' needs `name`: {_ACTION_ROLES[action]}."
        )
    if action in _ACTION_NEEDS_SOURCE and not (source and str(source).strip()):
        raise ForgeError(
            f"action '{action}' needs `source`: {_ACTION_ROLES[action]}."
        )

    if name is not None and str(name).strip():
        params["name"] = normalize_action_name(name)
    if source is not None and str(source).strip():
        params["source"] = normalize_action_name(source, label="source action name")
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if action in _ACTION_TAKES_LOOP:
        params["loop"] = bool(loop)

    result = blender_client.send_command("rigforge_action", params)

    bits = [f"'{params['name']}'"] if "name" in params else []
    if "source" in params:
        bits.insert(0, f"from '{params['source']}'")
    if action in _ACTION_TAKES_LOOP:
        bits.append("looping (-loop)" if loop else "one-shot")
    bits.append(f"rig {params['rig']}" if "rig" in params else "the active rig")
    return fmt_action_report(action, result, ", ".join(bits))


@app.tool()
def rigforge_keyframe(
    keys: List[Dict[str, Any]],
    action: Optional[str] = None,
    rig: Optional[str] = None,
    interpolation: Literal["BEZIER", "LINEAR"] = "BEZIER",
    clear: bool = False,
) -> str:
    """Set a batch of keyframes on control bones — the described-motion tool.

    THIS is how a description becomes animation. Sketch the pose at a few key
    frames and let the interpolation do the rest: a wave is the hand up at frame
    1, out at 8, back at 16, down at 24 — four keys, not twenty-four. Do not
    reach for execute_blender_python for this; one structured call keys every
    bone at once and reports the frame range it covered.

    `keys` is a list of `{"bone", "frame", ...}` objects, each moving at least
    one channel:
    - `rotation_euler_deg`: [x, y, z] in DEGREES (the add-on converts).
    - `location`: [x, y, z] in Blender units (metres), relative to the rest pose.
    - `scale`: [x, y, z], 1.0 being unscaled.
    A key with no bone, no frame, or no channel at all is refused here — it would
    reach Blender as a silent no-op.

      keys=[{"bone": "hand_ik.L", "frame": 1,  "location": [0, 0, 0]},
            {"bone": "hand_ik.L", "frame": 12, "location": [0, 0, 0.15]},
            {"bone": "hand_ik.L", "frame": 24, "location": [0, 0, 0]}]

    - `interpolation`: "BEZIER" (default) eases in and out — organic motion, and
      the reason a handful of keys reads as animation. "LINEAR" is constant
      speed: mechanical parts, conveyor belts, held poses.
    - `clear`: True wipes the action's existing keys first, so a re-run replaces
      the motion instead of layering onto it. Use it when iterating on the same
      description.
    - `action`: which clip to key; omit for the rig's current action. Open a
      named one with rigforge_action(action="new", ...) first — that is what
      makes it exportable as its own Godot clip.
    - `rig`: the armature; omit for the active/only one. Bone names are the
      CONTROL rig's (`hand_ik.L`, `spine_fk.003`), not the DEF- deform bones.
    """
    wanted = normalize_keys(keys)
    params: Dict[str, Any] = {
        "keys": wanted,
        "interpolation": interpolation,
        "clear": bool(clear),
    }
    if action is not None and str(action).strip():
        params["action"] = normalize_action_name(action)
    if rig and rig.strip():
        params["rig"] = rig.strip()

    result = blender_client.send_command("rigforge_keyframe", params)

    span = keys_frame_range(wanted)
    bones = sorted({str(key["bone"]) for key in wanted})
    summary = (
        f"{len(wanted)} key(s) on {len(bones)} bone(s)"
        + (f" over frames {span[0]}-{span[1]}" if span else "")
        + f", {interpolation}"
        + (", existing keys cleared" if clear else "")
    )
    subject = (
        f"'{params['action']}'" if "action" in params else "the rig's current action"
    )
    return fmt_keyframe_report(result, summary, subject)


@app.tool()
def rigforge_retarget(
    source_path: str,
    action_name: Optional[str] = None,
    target_rig: Optional[str] = None,
    mapping: Union[str, Dict[str, str]] = "auto",
    loop: bool = False,
    scale: Union[str, float] = "auto",
) -> str:
    """Transfer a motion-capture clip the user has onto this rig, as one action.

    Imports the file, maps its bones onto the rig's, transfers the rotations
    (plus the hip location), bakes the result into an action and deletes the
    import. Use it instead of rigforge_keyframe when real captured motion exists;
    the two produce the same kind of thing — an action in the library — so they
    mix freely on one character.

    `source_path` is a file the USER supplies: `.bvh` or `.fbx`, nothing else,
    and nothing is ever downloaded. Point it at a capture they already have on
    disk (Mixamo exports, a mocap session, a purchased pack). The path is
    resolved and must exist; another extension is refused rather than corrected,
    because the format is the file's, not ours.

    - `action_name`: what the clip becomes in the library. Omit it and the file's
      own name is used — pass one to follow the Godot convention (`run-loop`).
    - `mapping`: "auto" (default) matches bones by name heuristics and REPORTS
      what did not land — read that list, it is the whole story of a retarget.
      Pass `{"mixamorig:Hips": "torso", ...}` for the bones it gets wrong; only
      the entries you give override the heuristics.
    - `loop`: True for a cycle (walk, run, idle), which also drives the `-loop`
      name convention Godot reads.
    - `scale`: "auto" (default) fits the clip's proportions to the rig — a capture
      of a 1.8 m human on a 0.9 m goblin. A number is an explicit multiplier.
    - `target_rig`: the armature; omit for the active/only one.
    """
    clip = resolve_path(source_path, must_exist=True, label="source clip path")
    if clip.suffix.lower() not in MOCAP_SUFFIXES:
        raise ForgeError(
            f"{clip} is not a motion-capture clip Blender imports natively. "
            "rigforge_retarget reads .bvh or .fbx only (both are built in; "
            "nothing is downloaded). Convert the clip, or point at the .bvh/.fbx "
            "the user already has."
        )

    params: Dict[str, Any] = {
        "source_path": str(clip),
        "action_name": normalize_action_name(
            action_name if action_name is not None and str(action_name).strip()
            else action_name_for_clip(clip)
        ),
        "mapping": normalize_bone_mapping(mapping),
        "loop": bool(loop),
        "scale": normalize_retarget_scale(scale),
    }
    if target_rig and target_rig.strip():
        params["target_rig"] = target_rig.strip()

    result = blender_client.send_command("rigforge_retarget", params)

    given = params["mapping"]
    summary = (
        f"action '{params['action_name']}'"
        + (", looping (-loop)" if loop else ", one-shot")
        + (
            ", automatic bone mapping"
            if given == "auto"
            else f", {len(given)} explicit bone mapping(s)"
        )
        + (
            ", scale auto-fitted"
            if params["scale"] == "auto"
            else f", scale x{fmt_number(params['scale'])}"
        )
    )
    subject = f"'{target_rig.strip()}'" if target_rig and target_rig.strip() else "the rig"
    return fmt_retarget_report(clip.name, subject, result, summary)


def _walk_number(name: str, value: Any, minimum: float, maximum: float) -> float:
    """A `rigforge_walk` length/angle argument: a plain number in its own bound.

    The add-on clamps these too, but the failure has to read as "that number is
    out of range" here rather than as a mis-shapen walk discovered later.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForgeError(f"{name} must be a number (got {value!r}).")
    if not minimum <= float(value) <= maximum:
        raise ForgeError(
            f"{name} must be between {minimum} and {maximum} (got {value})."
        )
    return float(value)


def _fmt_walk_report(result: Mapping[str, Any], summary: str) -> str:
    """The cycle that was authored, the feet it planted, and what comes next."""
    action = result.get("action") or "(unnamed)"
    created = "new action" if result.get("created") else "existing action"
    lines = [
        f"Walk cycle on '{action}' ({created}) — {summary}",
        f"  {fmt_number(result.get('keys_set'), 0)} key(s) on "
        f"{fmt_number(len(result.get('bones') or []), 0)} bone(s), frames "
        f"{fmt_frame_range(result.get('frame_range'))}, "
        f"{result.get('interpolation') or '?'}",
    ]
    says = str(result.get("says") or "").strip()
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))
    feet = [f for f in (result.get("feet") or []) if isinstance(f, Mapping)]
    for foot in feet:
        lines.append(
            f"  {foot.get('foot', '?')} ({foot.get('target', '?')}): planted "
            f"{fmt_number(foot.get('stance_frames'), 0)} of "
            f"{fmt_number(result.get('cycle_frames'), 0)} frame(s)"
        )
    lines.append("  next: animation_check to measure foot slide on this clip.")
    return "\n".join(lines)


@app.tool()
def rigforge_walk(
    rig: Optional[str] = None,
    action: Optional[str] = None,
    cycle_frames: Optional[int] = None,
    step_length: Optional[float] = None,
    step_height: Optional[float] = None,
    stance_fraction: Optional[float] = None,
    hip_drop: Optional[float] = None,
    hip_sway: Optional[float] = None,
    hip_twist_deg: Optional[float] = None,
    arm_swing_deg: Optional[float] = None,
    elbow_bend_deg: Optional[float] = None,
    foot_roll_deg: Optional[float] = None,
    travel: bool = True,
    loop: bool = True,
    clear: bool = True,
    interpolation: Literal["LINEAR", "BEZIER"] = "LINEAR",
    stride_width: Optional[float] = None,
    reach_margin: Optional[float] = None,
) -> str:
    """Author a walk cycle on the leg IK targets, with the stance feet planted.

    THIS is locomotion. Keying `thigh_fk`/`shin_fk` by hand is the anti-pattern
    it exists to replace: an FK-keyed leg has nothing holding the foot on the
    ground between keys, so the character skates. `rigforge_walk` keys the feet
    on their IK targets instead, world-locked through the stance phase, and
    layers the hip bob, sway and twist, the arm swing and the heel/ball roll on
    top — sized off *this* rig's own leg length, so it works on a figurine and
    an ogre without being told which.

    Every length is metres, every angle degrees, on the CONTROL rig (the same
    one `rigforge_keyframe` uses). Omit any of them and the add-on sizes it off
    the rig's leg — do not guess a number to fill the gap.

    - `travel`: True (default) carries the root forward one stride per cycle —
      a root-motion clip; export it with `root_motion: true`. False authors it
      in place — the treadmill clip an engine plays under its own controller.
    - `loop`: True (default) marks it a cycle and applies Godot's `-loop` name
      convention (matching `rigforge_action`'s convention).
    - `clear`: True (default) wipes the action's existing keys first, so a
      re-run replaces the cycle instead of layering onto it.
    - `cycle_frames`: 4-600, the length of one stride.
    - `stance_fraction`: 0.2-0.95, how much of the cycle each foot spends planted.
    - `arm_swing_deg` / `elbow_bend_deg` / `foot_roll_deg` / `hip_twist_deg`: the
      secondary motion angles; each has its own bound (90 / 120 / 60 / 45 deg).
    - `reach_margin`: 0.5-1.2, how close to the leg's full reach a step is
      allowed to stretch before the add-on shortens the step or lowers the hips
      instead — read the warnings if it did either.
    - `step_length` / `step_height` / `hip_drop` / `hip_sway` / `stride_width`:
      metres; omit any of them for the add-on's own fraction of the leg.
    - `action`: the clip name (default "walk"); `rig`: the armature, omit for
      the active/only one.

    Run `animation_check` on the result and quote the drift in millimetres —
    this tool plants the feet, that one proves they held.
    """
    params: Dict[str, Any] = {
        "travel": bool(travel),
        "loop": bool(loop),
        "clear": bool(clear),
        "interpolation": interpolation,
    }
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if action and action.strip():
        params["action"] = action.strip()
    if cycle_frames is not None:
        if isinstance(cycle_frames, bool) or not isinstance(cycle_frames, int):
            raise ForgeError(f"cycle_frames must be a whole number (got {cycle_frames!r}).")
        if not 4 <= cycle_frames <= 600:
            raise ForgeError(f"cycle_frames must be between 4 and 600 (got {cycle_frames}).")
        params["cycle_frames"] = cycle_frames
    if stance_fraction is not None:
        params["stance_fraction"] = _walk_number("stance_fraction", stance_fraction, 0.2, 0.95)
    if reach_margin is not None:
        params["reach_margin"] = _walk_number("reach_margin", reach_margin, 0.5, 1.2)
    if arm_swing_deg is not None:
        params["arm_swing_deg"] = _walk_number("arm_swing_deg", arm_swing_deg, 0.0, 90.0)
    if elbow_bend_deg is not None:
        params["elbow_bend_deg"] = _walk_number("elbow_bend_deg", elbow_bend_deg, 0.0, 120.0)
    if foot_roll_deg is not None:
        params["foot_roll_deg"] = _walk_number("foot_roll_deg", foot_roll_deg, 0.0, 60.0)
    if hip_twist_deg is not None:
        params["hip_twist_deg"] = _walk_number("hip_twist_deg", hip_twist_deg, 0.0, 45.0)
    for label, value in (
        ("step_length", step_length),
        ("step_height", step_height),
        ("hip_drop", hip_drop),
        ("hip_sway", hip_sway),
        ("stride_width", stride_width),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ForgeError(f"{label} must be a number, in metres (got {value!r}).")
        if label != "stride_width" and float(value) < 0.0:
            raise ForgeError(f"{label} must be zero or positive, in metres (got {value}).")
        params[label] = float(value)

    result = blender_client.send_command("rigforge_walk", params)

    summary = (
        (f"{cycle_frames}-frame cycle" if cycle_frames is not None else "default-length cycle")
        + (", root motion" if travel else ", in place")
        + (", looping (-loop)" if loop else ", one-shot")
        + ("" if clear else ", layered onto existing keys")
    )
    return _fmt_walk_report(result, summary)


def _fmt_animation_check_report(result: Mapping[str, Any], summary: str) -> str:
    """The foot-slide gate: the verdict, the worst step, and each foot's table."""
    gate = str(result.get("gate") or "?")
    lines = [
        f"Foot-slide check — gate: {gate.upper()} ({summary})",
        f"  {result.get('mode') or '?'} mode ({result.get('mode_reason') or 'no reason given'})",
        f"  frames {fmt_frame_range(result.get('frames'))}, "
        f"{fmt_number(result.get('samples'), 0)} sample(s), "
        f"body travel {fmt_number(result.get('body_travel_mm'), 1)} mm",
    ]
    says = str(result.get("says") or "").strip()
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))

    feet = [f for f in (result.get("feet") or []) if isinstance(f, Mapping)]
    for foot in feet:
        worst = foot.get("worst_drift_mm")
        lines.append(
            "  {label:<10} steps={steps:>3}  worst drift={worst:>7}  {verdict}".format(
                label=str(foot.get("foot") or foot.get("bone") or "?")[:10],
                steps=fmt_number(foot.get("steps_measured"), 0),
                worst=("- mm" if worst is None else f"{fmt_number(worst, 1)} mm"),
                verdict=str(foot.get("verdict") or "?"),
            )
        )
    worst_step = result.get("worst_step")
    if isinstance(worst_step, Mapping):
        lines.append(
            f"  worst step: {worst_step.get('bone', '?')} frames "
            f"{fmt_frame_range(worst_step.get('frames'))}, "
            f"{fmt_number(worst_step.get('drift_mm'), 1)} mm"
        )
    tier = str(result.get("threshold_tier") or "").strip()
    if tier:
        lines.append(f"  {tier}")
    return "\n".join(lines)


@app.tool()
def animation_check(
    rig: Optional[str] = None,
    action: Optional[str] = None,
    mode: Literal["auto", "planted", "in_place", "jump", "airborne"] = "auto",
    frame_step: Optional[int] = None,
    contact_band: Optional[float] = None,
    min_stance_frames: Optional[int] = None,
    feet: Optional[List[str]] = None,
    airborne_clearance: Optional[float] = None,
    min_airborne_frames: Optional[int] = None,
    parabola_tolerance: Optional[float] = None,
    hop_tolerance_frames: Optional[int] = None,
) -> str:
    """Does the clip's planted foot HOLD? Measure drift, in millimetres, per step.

    Deterministic and geometric — no render, no model, nothing judged by eye. It
    samples every foot's contact point across the action, finds each stance
    (a run of frames where that point sits near its lowest point) and measures
    how far it drifts within that run. A few millimetres is planted; centimetres
    is skating. Works on any clip: `rigforge_walk`'s output, a hand-keyed
    `rigforge_keyframe` clip, `rigforge_punch`'s strike, `rigforge_jump`'s
    flight, or one `rigforge_retarget` imported from mocap. The rig's pose,
    action and the scene's current frame are all restored.

    A clip with an **airborne window** — a run of frames where every foot is
    off the ground *and* the body rose to put them there — is read as a jump:
    slide is not measured through the flight, the grounded takeoff/landing
    plants are measured raw either side of it, and the parabola, the landing
    knee and the plants are gated (plus, optionally, hop asymmetry). This is
    detected automatically (a walk or a punch cannot trip it), so a jump
    clip never needs to be labelled to be measured correctly.

    - `mode`: "auto" (default) tells a root-motion clip from an in-place one by
      how far the body travels against how far the feet swing, detects an
      airborne window the same way, and measures accordingly. Force "planted"
      (a travelling/root-motion clip — the foot should not move at all in
      world space), "in_place" (a treadmill clip — the feet are expected to
      run backwards at one shared speed), or "jump"/"airborne" (force the
      jump reading; refused loudly if the clip has no airborne window) when
      you already know which this is.
    - `feet`: bone names to measure (the toe/foot deform bones); omit to let the
      add-on find them by its own naming convention.
    - `frame_step`: 1-10, sample every Nth frame — coarser is faster on a long
      clip.
    - `contact_band`: 0.01-0.9, how close to a foot's lowest point still counts
      as "planted".
    - `min_stance_frames`: 2-1000, the shortest run of frames that counts as a
      stance rather than noise.
    - `airborne_clearance`: 0.01-0.9, how far above the clip's lowest contact
      sample a foot must sit — as a fraction of the contact points' vertical
      range — before it counts as off the ground.
    - `min_airborne_frames`: 2-1000, the shortest run of every-foot-off-ground
      frames that counts as flight rather than a sampling accident.
    - `parabola_tolerance`: 0.0005-1.0, how far the body's airborne height may
      sit off its own best-fit parabola, as a fraction of the rise over that
      window, before the gate calls the arc not ballistic.
    - `hop_tolerance_frames`: how long the clip may spend with exactly one foot
      down before hop asymmetry gates it; omit to leave that check off — a
      two-foot jump landing as a hop is only a problem when you say what you
      will tolerate.

    **The gate's thresholds are heuristics** — the scale at which skating becomes
    visible, not values calibrated against what an artist would accept. Quote
    the measurement next to the band that judged it, and never call a `fail` a
    fact about the animation.
    """
    params: Dict[str, Any] = {"mode": mode}
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if action and action.strip():
        params["action"] = action.strip()
    if frame_step is not None:
        if isinstance(frame_step, bool) or not isinstance(frame_step, int):
            raise ForgeError(f"frame_step must be a whole number (got {frame_step!r}).")
        if not 1 <= frame_step <= 10:
            raise ForgeError(f"frame_step must be between 1 and 10 (got {frame_step}).")
        params["frame_step"] = frame_step
    if contact_band is not None:
        params["contact_band"] = _walk_number("contact_band", contact_band, 0.01, 0.9)
    if min_stance_frames is not None:
        if isinstance(min_stance_frames, bool) or not isinstance(min_stance_frames, int):
            raise ForgeError(
                f"min_stance_frames must be a whole number (got {min_stance_frames!r})."
            )
        if not 2 <= min_stance_frames <= 1000:
            raise ForgeError(
                f"min_stance_frames must be between 2 and 1000 (got {min_stance_frames})."
            )
        params["min_stance_frames"] = min_stance_frames
    if airborne_clearance is not None:
        params["airborne_clearance"] = _walk_number(
            "airborne_clearance", airborne_clearance, 0.01, 0.9
        )
    if min_airborne_frames is not None:
        if isinstance(min_airborne_frames, bool) or not isinstance(min_airborne_frames, int):
            raise ForgeError(
                f"min_airborne_frames must be a whole number (got {min_airborne_frames!r})."
            )
        if not 2 <= min_airborne_frames <= 1000:
            raise ForgeError(
                f"min_airborne_frames must be between 2 and 1000 "
                f"(got {min_airborne_frames})."
            )
        params["min_airborne_frames"] = min_airborne_frames
    if parabola_tolerance is not None:
        params["parabola_tolerance"] = _walk_number(
            "parabola_tolerance", parabola_tolerance, 0.0005, 1.0
        )
    if hop_tolerance_frames is not None:
        if isinstance(hop_tolerance_frames, bool) or not isinstance(hop_tolerance_frames, int):
            raise ForgeError(
                f"hop_tolerance_frames must be a whole number (got {hop_tolerance_frames!r})."
            )
        if not 0 <= hop_tolerance_frames <= 1000:
            raise ForgeError(
                f"hop_tolerance_frames must be between 0 and 1000 "
                f"(got {hop_tolerance_frames})."
            )
        params["hop_tolerance_frames"] = hop_tolerance_frames
    names = [str(name).strip() for name in (feet or []) if str(name).strip()]
    if names:
        params["feet"] = names

    result = blender_client.send_command(
        "animation_check", params, read_timeout=config.PREVIEW_TIMEOUT
    )
    summary = (
        f"{mode} mode"
        + (f", every {frame_step} frame(s)" if frame_step else "")
        + (f", {len(names)} named foot/feet" if names else "")
    )
    return _fmt_animation_check_report(result, summary)


#: The addon's own default for `max_extension_ratio` (rigforge_anim.py's
#: MAX_EXTENSION_RATIO), mirrored here only so `reach_margin`'s upper bound can
#: be computed pre-flight when `max_extension_ratio` itself is omitted.
_PUNCH_MAX_EXTENSION_DEFAULT = 0.98

_PUNCH_SIDES = {"L": "L", "LEFT": "L", "R": "R", "RIGHT": "R"}


def _fmt_punch_report(result: Mapping[str, Any], summary: str) -> str:
    """The strike that was authored: chamber/strike frames, the three numbers
    the command refuses to leave unmeasured, and the feet that never moved."""
    action = result.get("action") or "(unnamed)"
    created = "new action" if result.get("created") else "existing action"
    side = {"L": "left", "R": "right"}.get(str(result.get("side")), "?")
    lines = [
        f"Punch on '{action}' ({created}) — {summary}",
        f"  {side} side, frames {fmt_frame_range(result.get('frame_range'))}, "
        f"chamber f{fmt_number(result.get('chamber_frame'), 0)} -> strike "
        f"f{fmt_number(result.get('strike_frame'), 0)}, "
        f"{fmt_number(result.get('keys_set'), 0)} key(s) on "
        f"{fmt_number(len(result.get('bones') or []), 0)} bone(s)",
    ]
    says = str(result.get("says") or "").strip()
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))

    lines.append(
        f"  peak fist speed {fmt_number(result.get('peak_fist_speed_m_per_s'), 2)} "
        f"m/s on frame {fmt_number(result.get('peak_fist_speed_frame'), 0)}"
    )
    extension_ratio = result.get("extension_ratio")
    lines.append(
        f"  extension {fmt_number(result.get('extension_m'), 3)} m of "
        f"{fmt_number(result.get('arm_reach_m'), 3)} m reach"
        + (
            f" ({fmt_number(extension_ratio * 100.0, 1)}% of reach, cap "
            f"{fmt_number((result.get('max_extension_ratio') or 0) * 100.0, 0)}%)"
            if extension_ratio is not None
            else ""
        )
        + ("" if result.get("extension_within_cap", True) else " — OVER CAP")
    )

    def _peak(name: str) -> str:
        entry = (result.get("rotation_lead") or {}).get(name) or {}
        degrees, frame = entry.get("degrees"), entry.get("frame")
        if degrees is None or frame is None:
            return "?"
        return f"{fmt_number(degrees, 1)} deg (f{frame})"

    lines.append(
        "  rotation lead: pelvis " + _peak("pelvis") + " -> chest " + _peak("chest")
        + " -> shoulder " + _peak("shoulder")
    )
    feet = result.get("feet_planted") or []
    if feet:
        lines.append("  feet planted (never moved): " + ", ".join(sorted(feet)))
    return "\n".join(lines)


@app.tool()
def rigforge_punch(
    rig: Optional[str] = None,
    action: Optional[str] = None,
    side: Optional[str] = None,
    frames: Optional[int] = None,
    strike_fraction: Optional[float] = None,
    lead_frames: Optional[int] = None,
    target_distance: Optional[float] = None,
    target_height: Optional[float] = None,
    hip_rotation_deg: Optional[float] = None,
    chest_rotation_deg: Optional[float] = None,
    shoulder_rotation_deg: Optional[float] = None,
    weight_shift: Optional[float] = None,
    hip_lower: Optional[float] = None,
    guard_rise: Optional[float] = None,
    chamber_draw: Optional[float] = None,
    reach_margin: Optional[float] = None,
    max_extension_ratio: Optional[float] = None,
    loop: bool = False,
    clear: bool = True,
    interpolation: Literal["LINEAR", "BEZIER"] = "LINEAR",
    poles: bool = True,
) -> str:
    """Author a jab/cross on the arm IK target, with both feet planted.

    THIS is combat authoring on the same plant `rigforge_walk` uses for
    locomotion — but where a walk moves the feet, a punch does the opposite: a
    boxer's stance shift is weight transfer, the hips travelling over
    stationary feet, so this command keys both foot IK targets at their rest
    position on every single frame. Unkeyed, a foot inherits whatever the
    pelvis does, and a pelvis that turns and slides drags an FK leg's foot
    with it — `animation_check` measures the stance at 0.0 mm by construction.

    The rest of the body is a kinetic chain: pelvis turns, then chest, then
    shoulder, then the fist arrives — each peak offset from the next by
    `lead_frames`, in that order by construction. The fist itself is keyed on
    the arm's IK target (a straight-line jab, not an FK arc) and its target is
    solved against the arm's own measured reach, so the same call fits a
    figurine and an ogre without being told which. The report never leaves
    three numbers unmeasured: peak fist speed (and the frame it happens on,
    which is before the strike — a fist still accelerating at impact is an arm
    being thrown, not punched), extension against the arm's own reach, and
    peak hip/chest rotation.

    Every length is metres, every angle degrees, and every default not given
    here is a fraction of *this* rig's own arm reach, shoulder width or leg
    length, measured off the rest pose — do not guess a number to fill a gap.

    - `side`: "L"/"LEFT" or "R"/"RIGHT" (default "R") — which arm throws.
    - `frames`: 8-600, the length of the whole clip (chamber, drive, recovery).
    - `strike_fraction`: 0.15-0.85, where in the clip the fist is at full
      extension.
    - `hip_rotation_deg` / `chest_rotation_deg` / `shoulder_rotation_deg`:
      0-60 / 0-60 / 0-45, the rotation cap for each link of the chain.
    - `max_extension_ratio`: 0.3-1.0, the hard ceiling on how far the fist may
      sit from the shoulder at full extension, as a fraction of the arm's
      measured reach — past it the elbow is hyperextended and Rigify's IK
      stretch makes up the difference.
    - `reach_margin`: 0.2 up to `max_extension_ratio`, what the target
      distance defaults to when `target_distance` is omitted.
    - `lead_frames`: how many frames each link leads the next by; omit for the
      add-on's own fraction of the clip.
    - `target_distance` / `target_height`: metres, where the fist is aimed at
      full extension; omit both to solve it from `reach_margin`.
    - `weight_shift` / `hip_lower`: metres, the hip travel over the planted
      feet and how far the hips lower to buy the stance it needs.
    - `guard_rise` / `chamber_draw`: metres, how the fist sits at guard and how
      far it draws back before firing.
    - `loop`: False (default) — a punch is a strike, not a cycle; True marks
      it a cycle and applies Godot's `-loop` name convention anyway.
    - `clear`: True (default) wipes the action's existing keys first.
    - `poles`: True (default) also keyframes the IK pole targets.
    - `action`: the clip name (default "punch.<side>"); `rig`: the armature,
      omit for the active/only one.

    Run `animation_check` on the result and quote the drift — this tool plants
    the feet, that one proves they held.
    """
    params: Dict[str, Any] = {
        "loop": bool(loop),
        "clear": bool(clear),
        "interpolation": interpolation,
        "poles": bool(poles),
    }
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if action and action.strip():
        params["action"] = action.strip()

    resolved_side: Optional[str] = None
    if side is not None:
        normalized = str(side).strip().upper()
        if normalized not in _PUNCH_SIDES:
            raise ForgeError(
                f"side must be one of L, LEFT, R, RIGHT (got {side!r})."
            )
        resolved_side = _PUNCH_SIDES[normalized]
        params["side"] = resolved_side

    if frames is not None:
        if isinstance(frames, bool) or not isinstance(frames, int):
            raise ForgeError(f"frames must be a whole number (got {frames!r}).")
        if not 8 <= frames <= 600:
            raise ForgeError(f"frames must be between 8 and 600 (got {frames}).")
        params["frames"] = frames
    if strike_fraction is not None:
        params["strike_fraction"] = _walk_number(
            "strike_fraction", strike_fraction, 0.15, 0.85
        )
    if hip_rotation_deg is not None:
        params["hip_rotation_deg"] = _walk_number(
            "hip_rotation_deg", hip_rotation_deg, 0.0, 60.0
        )
    if chest_rotation_deg is not None:
        params["chest_rotation_deg"] = _walk_number(
            "chest_rotation_deg", chest_rotation_deg, 0.0, 60.0
        )
    if shoulder_rotation_deg is not None:
        params["shoulder_rotation_deg"] = _walk_number(
            "shoulder_rotation_deg", shoulder_rotation_deg, 0.0, 45.0
        )
    if max_extension_ratio is not None:
        params["max_extension_ratio"] = _walk_number(
            "max_extension_ratio", max_extension_ratio, 0.3, 1.0
        )
    if reach_margin is not None:
        upper = (
            max_extension_ratio
            if max_extension_ratio is not None
            else _PUNCH_MAX_EXTENSION_DEFAULT
        )
        params["reach_margin"] = _walk_number("reach_margin", reach_margin, 0.2, upper)
    if lead_frames is not None:
        if isinstance(lead_frames, bool) or not isinstance(lead_frames, int):
            raise ForgeError(
                f"lead_frames must be a whole number (got {lead_frames!r})."
            )
        if lead_frames < 0:
            raise ForgeError(
                f"lead_frames must be zero or positive (got {lead_frames})."
            )
        params["lead_frames"] = lead_frames

    for label, value, allow_negative in (
        ("target_height", target_height, True),
        ("target_distance", target_distance, False),
        ("weight_shift", weight_shift, False),
        ("hip_lower", hip_lower, False),
        ("guard_rise", guard_rise, False),
        ("chamber_draw", chamber_draw, False),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ForgeError(f"{label} must be a number, in metres (got {value!r}).")
        if not allow_negative and float(value) < 0.0:
            raise ForgeError(
                f"{label} must be zero or positive, in metres (got {value})."
            )
        params[label] = float(value)

    result = blender_client.send_command(
        "rigforge_punch", params, read_timeout=config.PREVIEW_TIMEOUT
    )

    summary = (
        (f"{frames}-frame clip" if frames is not None else "default-length clip")
        + (f", {resolved_side} side" if resolved_side else "")
        + (", looping (-loop)" if loop else ", one-shot")
        + ("" if clear else ", layered onto existing keys")
    )
    return _fmt_punch_report(result, summary)


def _fmt_jump_report(result: Mapping[str, Any], summary: str) -> str:
    """The standing jump that was authored: apex reached against requested (and
    the residual the frame rounding leaves between keys), the airtime and how
    well it fit its own parabola, the leg extension against its cap, and the
    landing absorb depth against the anticipation crouch."""
    action = result.get("action") or "(unnamed)"
    created = "new action" if result.get("created") else "existing action"
    lines = [
        f"Jump on '{action}' ({created}) — {summary}",
        f"  frames {fmt_frame_range(result.get('frame_range'))}, "
        f"takeoff f{fmt_number(result.get('takeoff_frame'), 0)} -> apex "
        f"f{fmt_number(result.get('apex_frame'), 0)} -> land "
        f"f{fmt_number(result.get('landing_frame'), 0)}, "
        f"{fmt_number(result.get('keys_set'), 0)} key(s) on "
        f"{fmt_number(len(result.get('bones') or []), 0)} bone(s)",
    ]
    says = str(result.get("says") or "").strip()
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))

    apex_reached = result.get("apex_reached_m")
    apex_requested = result.get("apex_requested_m")
    apex_solved = result.get("apex_solved_m")
    lines.append(
        f"  apex {fmt_number((apex_reached or 0.0) * 1000.0, 0)} mm reached against "
        f"{fmt_number((apex_requested or 0.0) * 1000.0, 0)} mm requested (solved to "
        f"{fmt_number((apex_solved or 0.0) * 1000.0, 0)} mm by the frame rounding, "
        f"{fmt_number(result.get('apex_peak_between_keys_mm'), 3)} mm residual "
        "between keys)"
    )
    lines.append(
        f"  airtime {fmt_number(result.get('airborne_frames'), 0)} frame(s) "
        f"({fmt_number(result.get('airtime_s'), 3)} s), max parabola deviation "
        f"{fmt_number(result.get('parabola_deviation_mm'), 3)} mm"
        + (
            ""
            if result.get("parabola_within_tolerance", True)
            else " — OUT OF TOLERANCE"
        )
    )
    extension_ratio = result.get("extension_ratio")
    lines.append(
        "  extension peaks at "
        + (
            f"{fmt_number(extension_ratio * 100.0, 1)}%"
            if extension_ratio is not None
            else "?"
        )
        + f" of a {fmt_number(result.get('leg_reach_m'), 3)} m reach (cap "
        f"{fmt_number((result.get('max_extension_ratio') or 0) * 100.0, 0)}%)"
        + ("" if result.get("extension_within_cap", True) else " — OVER CAP")
    )
    lines.append(
        f"  landing absorbs {fmt_number(result.get('absorb_measured_m'), 3)} m "
        f"against a {fmt_number(result.get('crouch_measured_m'), 3)} m "
        "anticipation crouch"
        + ("" if result.get("absorb_deeper_than_crouch", True) else " — NOT DEEPER")
    )
    feet = result.get("feet_planted") or []
    if feet:
        lines.append(
            "  feet planted (takeoff & landing): " + ", ".join(sorted(feet))
        )
    return "\n".join(lines)


@app.tool()
def rigforge_jump(
    rig: Optional[str] = None,
    action: Optional[str] = None,
    frames: Optional[int] = None,
    apex_height: Optional[float] = None,
    jump_distance: Optional[float] = None,
    crouch_depth: Optional[float] = None,
    landing_depth: Optional[float] = None,
    tuck_height: Optional[float] = None,
    anticipation_fraction: Optional[float] = None,
    launch_fraction: Optional[float] = None,
    landing_fraction: Optional[float] = None,
    recover_fraction: Optional[float] = None,
    gravity: Optional[float] = None,
    chest_pitch_deg: Optional[float] = None,
    arm_swing_back_deg: Optional[float] = None,
    arm_swing_up_deg: Optional[float] = None,
    elbow_bend_deg: Optional[float] = None,
    foot_roll_deg: Optional[float] = None,
    landing_strike_ratio: Optional[float] = None,
    max_extension_ratio: Optional[float] = None,
    loop: bool = False,
    clear: bool = True,
    interpolation: Literal["LINEAR", "BEZIER"] = "LINEAR",
    poles: bool = True,
) -> str:
    """Author a standing jump on the leg IK targets, whose airtime is derived
    from `apex_height` rather than a frame count for the flight.

    THIS is the first locomotion clip whose feet are *supposed* to leave the
    ground, so it is the first one that can be wrong by floating rather than
    by sliding. A body in flight is being integrated, not animated: from
    takeoff to landing the only thing acting on the root is gravity, so
    `apex_height` is the parameter and the airtime falls out of it — this
    command does not take a frame count for the flight. The requested airtime
    is rounded to whole frames so the landing closes exactly on a key, and the
    apex is solved back out of that rounded airtime; the report quotes both
    numbers so the difference is visible instead of silent. Legs are keyed on
    their IK targets (the plants at takeoff and landing), arms swing on FK,
    the launch extension is capped against the legs' own measured reach the
    same way `rigforge_punch` caps the fist, and the landing absorb is deeper
    than the anticipation crouch by default because catching a falling body
    takes more travel than launching a standing one.

    Every length is metres, every angle degrees, and every default not given
    here is a fraction of *this* rig's own leg length, measured off the rest
    pose — do not guess a number to fill a gap.

    - `frames`: 12-600, the length of the whole clip (guard, anticipation,
      launch, flight, landing, recover) — lengthened automatically if the
      flight plus the grounded phases will not fit.
    - `apex_height`: metres, how high the hips rise at the top of the arc;
      omit for 35% of the leg length. Zero or positive only here — the
      add-on owns the rig-relative floor/ceiling (1e-4 m up to 5x leg length)
      and reports if it had to resolve the requested value.
    - `jump_distance` / `crouch_depth` / `landing_depth` / `tuck_height`:
      metres; zero or positive only here for the same reason — each has its
      own rig-relative cap the add-on enforces and explains in a warning if
      it had to clamp.
    - `anticipation_fraction` / `launch_fraction` / `landing_fraction` /
      `recover_fraction`: 0.05-0.5 / 0.02-0.3 / 0.02-0.4 / 0.05-0.5, each
      grounded phase's share of the clip. The airborne window is not one of
      these fractions — it is derived from `apex_height`.
    - `gravity`: 0.1-100 m/s^2, the jump's own clock; a low-gravity jump is a
      real art direction and the point of deriving the timing is that it
      stays derived.
    - `chest_pitch_deg` / `arm_swing_back_deg` / `arm_swing_up_deg` /
      `elbow_bend_deg` / `foot_roll_deg`: 0-45 / 0-90 / 0-170 / 0-120 / 0-60,
      the secondary motion angles.
    - `landing_strike_ratio`: 0-1, the heel-strike angle at landing contact as
      a fraction of `foot_roll_deg` — small by default, because the roll
      pivots about the heel and lifts the ball, the very point the foot-slide
      metric measures.
    - `max_extension_ratio`: 0.3-1.0, the hard ceiling on hip-to-ankle
      distance at full extension, as a fraction of the leg's own measured
      reach — past it the knee is hyperextended and Rigify's IK stretch makes
      up the difference.
    - `loop`: False (default) — a jump is a beat, not a cycle; True marks it a
      cycle and applies Godot's `-loop` name convention anyway.
    - `clear`: True (default) wipes the action's existing keys first.
    - `poles`: True (default) also keyframes the IK pole targets.
    - `action`: the clip name (default "jump", or "jump-forward" when
      `jump_distance` is given); `rig`: the armature, omit for the
      active/only one.

    Run `animation_check` on the result and pass `mode="jump"` (or leave it on
    "auto" — a clip with an airborne window is detected as one) to gate the
    plants, the parabola and the landing knee.
    """
    params: Dict[str, Any] = {
        "loop": bool(loop),
        "clear": bool(clear),
        "interpolation": interpolation,
        "poles": bool(poles),
    }
    if rig and rig.strip():
        params["rig"] = rig.strip()
    if action and action.strip():
        params["action"] = action.strip()

    if frames is not None:
        if isinstance(frames, bool) or not isinstance(frames, int):
            raise ForgeError(f"frames must be a whole number (got {frames!r}).")
        if not 12 <= frames <= 600:
            raise ForgeError(f"frames must be between 12 and 600 (got {frames}).")
        params["frames"] = frames
    if anticipation_fraction is not None:
        params["anticipation_fraction"] = _walk_number(
            "anticipation_fraction", anticipation_fraction, 0.05, 0.5
        )
    if launch_fraction is not None:
        params["launch_fraction"] = _walk_number(
            "launch_fraction", launch_fraction, 0.02, 0.3
        )
    if landing_fraction is not None:
        params["landing_fraction"] = _walk_number(
            "landing_fraction", landing_fraction, 0.02, 0.4
        )
    if recover_fraction is not None:
        params["recover_fraction"] = _walk_number(
            "recover_fraction", recover_fraction, 0.05, 0.5
        )
    if gravity is not None:
        params["gravity"] = _walk_number("gravity", gravity, 0.1, 100.0)
    if chest_pitch_deg is not None:
        params["chest_pitch_deg"] = _walk_number(
            "chest_pitch_deg", chest_pitch_deg, 0.0, 45.0
        )
    if arm_swing_back_deg is not None:
        params["arm_swing_back_deg"] = _walk_number(
            "arm_swing_back_deg", arm_swing_back_deg, 0.0, 90.0
        )
    if arm_swing_up_deg is not None:
        params["arm_swing_up_deg"] = _walk_number(
            "arm_swing_up_deg", arm_swing_up_deg, 0.0, 170.0
        )
    if elbow_bend_deg is not None:
        params["elbow_bend_deg"] = _walk_number(
            "elbow_bend_deg", elbow_bend_deg, 0.0, 120.0
        )
    if foot_roll_deg is not None:
        params["foot_roll_deg"] = _walk_number(
            "foot_roll_deg", foot_roll_deg, 0.0, 60.0
        )
    if landing_strike_ratio is not None:
        params["landing_strike_ratio"] = _walk_number(
            "landing_strike_ratio", landing_strike_ratio, 0.0, 1.0
        )
    if max_extension_ratio is not None:
        params["max_extension_ratio"] = _walk_number(
            "max_extension_ratio", max_extension_ratio, 0.3, 1.0
        )

    for label, value in (
        ("apex_height", apex_height),
        ("jump_distance", jump_distance),
        ("crouch_depth", crouch_depth),
        ("landing_depth", landing_depth),
        ("tuck_height", tuck_height),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ForgeError(f"{label} must be a number, in metres (got {value!r}).")
        if float(value) < 0.0:
            raise ForgeError(
                f"{label} must be zero or positive, in metres (got {value})."
            )
        params[label] = float(value)

    result = blender_client.send_command(
        "rigforge_jump", params, read_timeout=config.PREVIEW_TIMEOUT
    )

    summary = (
        (f"{frames}-frame clip" if frames is not None else "default-length clip")
        + (", looping (-loop)" if loop else ", one-shot")
        + ("" if clear else ", layered onto existing keys")
    )
    return _fmt_jump_report(result, summary)


@app.tool()
def rigforge_status(object: Optional[str] = None) -> str:
    """Where is this mesh in the RigForge pipeline, and what is the next call?

    One call that answers "what have we done to this character so far": the
    object's vertex/face counts, every body-part tag with its size, whether a
    retopo or LOD mesh exists alongside it, whether a metarig or a generated rig
    exists, and — once a rig does — what is in its action library. Then the one
    next step in the chain (tag -> retopo -> auto_uv -> metarig -> generate_rig
    -> [cloth] -> action/keyframe/retarget -> export_godot). Start here before
    tagging, retopologising, rigging or animating something you did not just
    create.

    Omit `object` to report on Blender's active object.
    """
    scene = blender_client.send_command("get_scene_info")
    name = (object or "").strip() or str(scene.get("active") or "").strip()
    if not name:
        raise ForgeError(
            "No object given and nothing is active in Blender. Name the object, "
            "or select one first (select_object)."
        )

    entry = scene_object(scene, name)
    if entry is None:
        available = ", ".join(
            str(o.get("name")) for o in (scene.get("objects") or []) if isinstance(o, dict)
        )
        raise ForgeError(
            f"No object named '{name}' in the scene. Present: {available or '(none)'}."
        )

    faces = entry.get("face_count")
    lines = [
        f"RigForge status — {name}" + (" (active)" if name == scene.get("active") else ""),
        f"  {entry.get('type', '?')}: {entry.get('vertex_count', '?')} verts, "
        + (f"{faces} faces" if faces is not None else "face count not reported")
        + f", dimensions {fmt_vector(entry.get('dimensions'), 3)}",
    ]

    has_tags = False
    try:
        tags = blender_client.send_command("rigforge_list_tags", {"object": name}).get("tags")
    except ForgeError as exc:
        lines.append(f"  tags: unavailable — {exc}")
    else:
        listed = [t for t in (tags or []) if isinstance(t, dict)]
        has_tags = bool(listed)
        if not listed:
            lines.append("  tags: none yet — tag body parts with rigforge_tag")
        else:
            lines.append(f"  tags ({len(listed)}):")
            for tag in listed:
                lines.append(
                    f"    {str(tag.get('name', '?')):<24.24} "
                    f"{str(tag.get('vertex_count', '?')):>8} verts "
                    f"{str(tag.get('face_count', '?')):>8} faces"
                )

    siblings = derivative_objects(scene, name)
    if siblings:
        lines.append(f"  retopo/LOD siblings ({len(siblings)}):")
        for sibling in siblings:
            info = scene_object(scene, sibling) or {}
            sibling_faces = info.get("face_count")
            lines.append(
                f"    {sibling:<24.24} {str(info.get('vertex_count', '?')):>8} verts"
                + (f" {sibling_faces:>8} faces" if sibling_faces is not None else "")
            )
    else:
        lines.append("  retopo/LOD siblings: none — run rigforge_retopo when tagging is done")

    armatures = rig_objects(scene, name)
    metarigs, rigs = armatures["metarig"], armatures["rig"]
    if metarigs or rigs:
        lines.append(
            "  armatures: "
            + ("metarig " + ", ".join(metarigs) if metarigs else "no metarig")
            + " / "
            + ("rig " + ", ".join(rigs) if rigs else "no generated rig")
        )
    else:
        lines.append(
            "  armatures: none — run rigforge_metarig once the retopo mesh is tagged"
        )

    # The action library only exists once a rig does, and only an add-on with the
    # Phase 5 commands can answer — so this is best-effort, exactly like the tag
    # call above. `None` means "could not ask", which is NOT the same as "empty".
    has_actions: Optional[bool] = None
    if rigs:
        try:
            listed = blender_client.send_command(
                "rigforge_action", {"action": "list", "rig": rigs[0]}
            ).get("actions")
        except ForgeError as exc:
            lines.append(f"  actions: unavailable — {exc}")
        else:
            names = action_names(listed)
            has_actions = bool(names)
            if names:
                lines.append(f"  actions ({len(names)}): " + ", ".join(names))
            else:
                lines.append(
                    "  actions: none yet — open one with rigforge_action and "
                    "sketch it with rigforge_keyframe, or import a clip with "
                    "rigforge_retarget"
                )

    lines.append("")
    lines.append(
        "  next: "
        + next_rig_step(
            has_tags=has_tags,
            has_retopo=bool(siblings),
            has_metarig=bool(metarigs),
            has_rig=bool(rigs),
            has_actions=has_actions,
        )
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mechanism demos (Phase 17) — show how it works
# ---------------------------------------------------------------------------
#
# The three tools that turn a build plan into the thing the robotics sites do:
# the part presses, the LED comes on, and a two-second film shows both. It is
# playback of numbers the maker tools already computed — never a simulation, and
# every report here says so, because a demo that looks like physics and is not
# is the one way this could mislead an artist about their own design.


@app.tool()
def animate_object(
    keys: List[Dict[str, Any]],
    object: Optional[str] = None,
    interpolation: Literal["BEZIER", "LINEAR", "CONSTANT"] = "BEZIER",
    clear: bool = False,
) -> str:
    """Keyframe an object's own transform — the press stroke, in one call.

    `rigforge_keyframe`'s sibling: that one poses bones, this one moves a whole
    object. It is how a mechanism demo is built — the plunger travelling down and
    springing back, a lid hinging open, a slide advancing.

    **`location_mm` is the unit to use.** Every number in this toolchain is a
    millimetre: `plunger_plan` says the cap travels 2.1 mm, and
    `{"frame": 8, "location_mm": [0, 0, -2.1]}` is that sentence with nothing to
    convert and nothing to get wrong. Take the numbers from the plan — a demo
    with an invented stroke is a lie about the design.

      keys=[{"frame": 1,  "location_mm": [0, 0, 0]},
            {"frame": 8,  "location_mm": [0, 0, -2.1]},
            {"frame": 20, "location_mm": [0, 0, 0]}]

    - `keys`: `{"frame", ...}` objects, each moving at least one channel:
      `location_mm` (millimetres — prefer this), `location` (metres, Blender's
      own unit; giving both for one key is an error, not a merge),
      `rotation_euler_deg` (degrees; forces XYZ euler mode and reports it),
      `scale`.
    - `interpolation`: "BEZIER" (default) eases in and out — a spring return.
      "LINEAR" is constant speed (a motor, a slide). "CONSTANT" snaps between
      poses (a latch).
    - `clear`: True wipes the object's existing animation first, which is what
      makes re-running a demo idempotent rather than a layered mess.
    - `object`: omit for the active object.

    The whole batch is parsed before any of it is applied, so a typo in keys[7]
    cannot leave keys[0..6] half done. Follow with set_material_emission for
    anything that lights up, then render_animation over the reported range.
    """
    wanted = normalize_object_keys(keys)
    params = _target(object)
    params["keys"] = wanted
    params["interpolation"] = interpolation
    params["clear"] = bool(clear)

    result = blender_client.send_command("animate_object", params)

    span = object_keys_frame_range(wanted)
    summary = (
        f"{len(wanted)} key(s)"
        + (f" over frames {span[0]}-{span[1]}" if span else "")
        + f", {interpolation}"
        + (", existing animation cleared" if clear else "")
    )
    subject = f"'{object}'" if object else "the active object"
    return fmt_animate_report(subject, result, summary)


@app.tool()
def set_material_emission(
    strength: float,
    object: Optional[str] = None,
    color: Optional[List[float]] = None,
    frame: Optional[int] = None,
    interpolation: Literal["CONSTANT", "LINEAR", "BEZIER"] = "CONSTANT",
) -> str:
    """Make something glow — the LED coming on, keyframed at the moment it does.

    With no `frame` this is a STATE: the thing is lit, now, and a
    render_preview(shading="material") shows it. With a `frame` it is an EVENT:
    `strength=0` at the frame before the click and `strength=6` at the click is
    an LED coming on when the latch catches.

    - `strength`: 0-1000. 0 is off; 5-20 reads as a lit LED in EEVEE.
    - `color`: `[r, g, b]` in **0-1**, not 0-255. A warm amber LED is about
      [1.0, 0.62, 0.2]. Omit to leave the colour alone.
    - `frame`: the frame to key at. Omit for a state.
    - `interpolation`: "CONSTANT" (default) is correct for an LED — it is off and
      then it is on, it does not fade up over seven frames. Use "LINEAR" or
      "BEZIER" only for the deliberate exception (a heater, a charge indicator).
    - `object`: omit for the active object.

    Three ways into a material, in order of how little they disturb: an emission
    node that is already there, a Principled BSDF's own emission inputs (so an
    existing look survives), or a new emission node. An object with no material
    at all gets a plain one called "Forge Glow", because an LED lens reads as a
    light and not as a lit surface.

    Emission only shows with materials: render_animation(engine="eevee") or
    render_preview(shading="material"). Workbench draws clay and shows nothing.
    """
    value = normalize_emission_strength(strength)
    rgb = normalize_emission_color(color)

    params = _target(object)
    params["strength"] = value
    if rgb is not None:
        params["color"] = rgb
    if frame is not None:
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise ForgeError(
                f"frame must be a whole frame number (got {frame!r})."
            )
        params["frame"] = int(frame)
        params["interpolation"] = interpolation

    result = blender_client.send_command("set_material_emission", params)

    summary = f"strength {fmt_number(value, 2)}"
    if rgb is not None:
        summary += f", colour {fmt_vector(rgb, 2)}"
    summary += (f", keyed at frame {frame} ({interpolation})"
                if frame is not None else ", not keyed")
    subject = f"'{object}'" if object else "the active object"
    return fmt_emission_report(subject, result, summary)


@app.tool()
def render_animation(
    frame_start: int,
    frame_end: int,
    project: Optional[str] = None,
    name: Optional[str] = None,
    objects: Optional[List[str]] = None,
    view: Literal["iso", "front", "side", "top"] = "iso",
    fps: Optional[int] = None,
    resolution: Optional[int] = None,
    engine: Literal["eevee", "workbench"] = "eevee",
) -> str:
    """The demo itself: render the keyed motion to a short .mp4 and hand it over.

    This is the payoff of animate_object + set_material_emission — the thing the
    robotics sites do, showing a mechanism working instead of describing it. Then
    **name the returned path in your reply**: the bridge serves .mp4, so the film
    plays inline in the artist's chat and on the project's Library card.

    There is no `path` parameter, deliberately — this picks its own folder, the
    way render_preview does. You name the FILE, never the location.

    - `project`: files the demo in `projects/<slug>/renders/`, which is where the
      Library plays it from. Pass it whenever the demo belongs to a project (it
      almost always does); omit it for a throwaway and it lands in the scratch
      previews folder.
    - `name`: what to call it, in plain words — "litwick press" becomes
      `litwick-press.mp4`. Name it for what it SHOWS, never `demo1`. Re-rendering
      the same name replaces that take, which is what makes iterating on a demo
      free; omit `name` and the film is numbered instead.
    - `frame_start` / `frame_end`: the range animate_object reported as
      `frame_range`. At most 600 frames — a demo is a couple of seconds.
    - `objects`: names to film; omit for every visible mesh.
    - `view`: "iso" (default) or "front"/"side"/"top" — the same projections
      render_preview and load_reference use. Film the view the motion is IN: a
      2 mm vertical press is invisible from the top.
    - `fps`: 1-60, default 24. `resolution`: 128-1920 square, default 640.
    - `engine`: "eevee" (default — the only one that shows an emission, so the
      only one that shows the LED) or "workbench" (clay, but seconds faster and
      it never waits on shader compilation).

    Read-only: the camera, the render settings and the artist's viewport are all
    borrowed and put back, and the one thing left behind is the file. The camera
    is fitted over the WHOLE clip, not frame one, so nothing presses itself out
    of shot.

    **Budget the wait honestly.** The first EEVEE render of a Blender session
    pays for shader compilation and every one after it does not: 48 frames at
    640 px measured 53 s cold and 7 s warm. Workbench compiles nothing (~1-2 s)
    and is the right answer when the motion, not the light, is the point.

    Say what it IS when you show it: an illustration of the intended motion built
    from the plan's numbers — not a simulation. Nothing here computes a force, a
    spring rate or a collision.
    """
    start, end = normalize_animation_frames(frame_start, frame_end)
    rate = normalize_animation_fps(fps)
    pixels = normalize_animation_resolution(resolution)
    names = normalize_preview_objects(objects)
    out = animation_path(project, view, name)

    params: Dict[str, Any] = {
        "path": str(out),
        "frame_start": start,
        "frame_end": end,
        "fps": rate,
        "resolution": pixels,
        "engine": engine,
        "view": view,
    }
    if names:
        params["objects"] = names

    result = blender_client.send_command(
        "render_animation", params, read_timeout=config.PREVIEW_TIMEOUT
    )
    result.setdefault("path", str(out))

    frames = end - start + 1
    summary = (
        f"frames {start}-{end} ({frames}) at {rate} fps, {pixels} px, "
        f"{engine}, {view} view"
        + (f", filed in projects/{project_slug(project)}/"
           f"{util.PROJECT_RENDERS_DIRNAME}/" if project else ", scratch folder")
    )
    return fmt_animation_report(result, summary)


# ---------------------------------------------------------------------------
# Floor plans (Phase 19) — draw rooms, get walls
# ---------------------------------------------------------------------------
#
# THE PLAN FILE IS THE MODEL. There is no generative 3D anywhere in this group:
# `projects/<slug>/design/floorplan.json` carries the whole meaning of the
# drawing, the greybox is a projection of it, and every id in it is forever
# because an id is what names the Blender object and therefore what makes an
# edit a DIFF rather than a regen.
#
# The order is fixed and the middle step is not optional: extract (when there is
# a drawing — the geometry is MEASURED, because the one time it was eyeballed
# the level came out with the wrong footprint and a diagonal wall that exists
# nowhere in the picture) -> validate (read it back, resolve the numbers, show
# the appliance matches) -> the echo-back `floorplan.svg` the artist approves ->
# build. After that, every edit goes
# through `floorplan_diff` first so the reply can say WHICH ids rebuild, because
# "only wall-03 rebuilds" is a promise about the hand-sculpted sofa in the next
# room surviving, and that promise is worth more than the level being right.


def _plan_source(
    plan: Optional[Dict[str, Any]],
    project: Optional[str],
    *,
    tool: str,
) -> tuple[Optional[str], Dict[str, Any], str]:
    """`(slug|None, plan, where it came from)` from a plan object or a project.

    Exactly one of the two is required, and giving both is not an error: it is
    the normal editing shape — the proposed plan, plus the project it belongs
    to, which is where a validated copy gets saved back.
    """
    if plan is None and project is None:
        raise ForgeError(
            f"{tool} needs a plan. Pass the floorplan.json object as `plan`, or "
            "name the `project` whose design/floorplan.json to read. The shape "
            'is {"version": 1, "units": "mm", "defaults": {...}, "rooms": '
            '[...], "walls": [...], "labels": [...]}, with a stable id on every '
            "entry."
        )
    if plan is not None:
        if not isinstance(plan, dict):
            raise ForgeError(
                f"`plan` must be the floor-plan object itself, not a "
                f"{type(plan).__name__}. To use a project's saved plan, pass "
                "`project` instead of a path."
            )
        if project is not None:
            slug, _path = util.floorplan_path(project)
            return slug, plan, "the plan you passed"
        return None, plan, "the plan you passed"
    slug, path, saved = util.read_floorplan(project)
    return slug, saved, str(path)


@app.tool()
def floorplan_extract(
    image_path: str,
    project: Optional[str] = None,
    mm_per_px: Optional[float] = None,
    legend: Optional[Dict[str, str]] = None,
    grid_px: Optional[float] = None,
    save: bool = True,
) -> str:
    """Read a DRAWING of a floor plan into a plan file. Measured, not eyeballed.

    **If they gave you a picture, this is how you read it.** Hand-authoring
    `floorplan.json` coordinates from a drawing is forbidden and it is forbidden
    because it was tried: the level came out with the wrong footprint, the rooms
    in the wrong places, and a diagonal wall that exists nowhere in the drawing.
    Colour classification, connected components and a boundary walk on the pixel
    lattice do not make that mistake — **a diagonal is impossible here by
    construction**, because every edge is traced as axis-aligned pixel steps and
    then snapped to the drawing's own grid.

    - `image_path`: the drawing on this machine (.png is the expected export).
    - `project`: file the plan under `projects/<slug>/design/floorplan.json`
      (`save=false` reads without writing).
    - `mm_per_px`: the scale. **Leave it out the first time** — the report comes
      back with the ONE calibration question and the pixel lengths to divide,
      and you call this again with their answer. Never scale the numbers
      yourself.
    - `legend`: `{"#4285f4": "house_door", "#34a853": "doorway", "#db4437":
      "room_door"}` — which colour is which kind of opening. Without it the
      swatches in the drawing's own key are found geometrically (a colour blob
      touching no room) and their roles are ASSUMED from hue, named as
      assumptions in the report. Roles: house_door, room_door, door, doorway,
      gap, window.
    - `grid_px`: the drawing's grid pitch when it is known; detected otherwise.
      No coordinate ever moves more than a few pixels to reach it.

    **Geometry comes back from here; NAMES come back from you.** Rooms are
    `room-r1`..`room-rN` in reading order with no labels, and each one carries a
    crop box: look at that part of the drawing, read the word in it, and set the
    room's `label`. Never change an id — an id names a Blender object, and a
    renamed id deletes the artist's work.

    Then render `design/floorplan.svg` FROM THIS PLAN (never from the picture),
    name its path, gate on their approval, and build.
    """
    image = util.resolve_path(image_path, must_exist=True, label="drawing path")

    slug: Optional[str] = None
    if project is not None:
        slug, _path = util.floorplan_path(project)

    result = floorplan.extract(str(image), legend=legend, mm_per_px=mm_per_px,
                               grid_px=grid_px)

    # Validated on the way out for the same reason `floorplan_validate` exists:
    # a plan that cannot be built should be a refusal here, not a surprise two
    # turns later. The extractor validates its own output too; this is the
    # server agreeing rather than assuming.
    normalized = floorplan.validate(result["plan"])

    saved_path: Optional[Path] = None
    overwritten = False
    if slug is not None and save:
        saved_path, overwritten = util.write_floorplan(slug, normalized)

    return floorplan.fmt_extract_report(result, image=image, saved=saved_path,
                                        overwritten=overwritten, slug=slug)


@app.tool()
def floorplan_validate(
    plan: Optional[Dict[str, Any]] = None,
    project: Optional[str] = None,
    save: bool = True,
) -> str:
    """Check a floor plan against the schema and report what Forge read in it.

    The first call of the floor-plan flow and the one that has to happen before
    anything is drawn or built: it resolves every optional number, matches each
    fixture label against the appliance table, and hands back the reading in
    words the artist can correct — *"I read 'W/D' as a washer/dryer, 850 mm
    tall, on an alias match"* is a sentence they fix in one word on the sheet,
    and fixing it after the level is built costs a rebuild.

    - `plan`: the floor-plan object. `project`: the project whose saved
      `design/floorplan.json` to read. One of the two, or both — both is the
      editing shape (validate this proposal, and file it under that project).
    - `save`: with a `project`, the **normalised** plan is written back to
      `projects/<slug>/design/floorplan.json` under `save_design_doc`'s rules.
      Normalised, not resolved: the `defaults` block stays live, so a number the
      artist never chose stays a default and raising `defaults.ceiling_mm` later
      still moves every wall that takes its height from it.

    **Refusals are the feature.** A duplicate id, an opening past the end of its
    wall or taller than it, two openings on top of each other, a room that
    crosses itself, a missing id — each comes back as a sentence naming the
    entry. A missing id is refused rather than generated, because an invented id
    changes next run and takes the artist's hand edits with it.

    Nothing is built by this call. The echo-back gate comes next: hand-author
    `design/floorplan.svg` from these numbers, name its path so it renders in
    their chat, and build only after they say yes.
    """
    slug, source_plan, source = _plan_source(plan, project, tool="floorplan_validate")

    # The settings sheet settles HERE, at read time, off disk — never from an
    # argument a model filled in from a number it remembered. Precedence is
    # plan, then sheet, then the service's own DEFAULTS: the plan's explicit
    # block always wins, because a number the artist typed into this plan is a
    # decision about this plan.
    from_settings: List[str] = []
    if slug is not None:
        settled = task_config.plan_defaults(slug)
        if settled:
            block = dict(source_plan.get("defaults") or {})
            for name, value in settled.items():
                if name not in block:
                    block[name] = value
                    from_settings.append(name)
            if from_settings:
                source_plan = dict(source_plan)
                source_plan["defaults"] = block

    # Normalised first, resolved second, and they are DIFFERENT documents: the
    # normalised one is what gets saved (it still knows which numbers were
    # chosen), the resolved one is what gets reported and, later, built.
    normalized = floorplan.validate(source_plan)
    resolved = floorplan.resolve(normalized)

    saved_path: Optional[Path] = None
    overwritten = False
    if slug is not None and save:
        saved_path, overwritten = util.write_floorplan(slug, normalized)

    return fmt_floorplan_validated(
        slug=slug,
        source=source,
        resolved=resolved,
        tally=floorplan.counts(resolved),
        matches=floorplan.appliance_matches(resolved),
        defaulted=floorplan.from_defaults(resolved),
        saved=saved_path,
        overwritten=overwritten,
        from_settings=from_settings,
        pipeline=pipeline.mention(slug) if slug else "",
    )


@app.tool()
def floorplan_diff(
    plan: Dict[str, Any],
    against: Optional[Dict[str, Any]] = None,
    project: Optional[str] = None,
) -> str:
    """What would this edit actually rebuild? Ask BEFORE floorplan_build.

    The incremental-regen law, made sayable. Two plans go in and four lists of
    ids come out — added, removed, changed, unchanged — so the reply can be
    *"this edit rebuilds 2 walls, adds 1 fixture, and touches nothing else"*
    instead of an unqualified "rebuilding the level". Every id NOT named keeps
    the object it already has, hand edits included.

    - `plan`: the proposed (new) plan.
    - `against`: the plan to compare it with, or `project`: compare against that
      project's saved `design/floorplan.json`. One of the two.

    Both sides are resolved before comparing, so the diff is of real values:
    raising `defaults.ceiling_mm` marks every wall that was taking its height
    from the block and leaves a wall with its own explicit height alone, and
    dropping an explicit `thickness_mm: 100` when the default is already 100
    marks nothing. An opening's change is its wall's change, because a door is a
    hole cut in one — a door that moves between walls marks both.

    Reads nothing in Blender and builds nothing: this is arithmetic on two
    documents, and it is cheap enough to run on every edit.
    """
    if not isinstance(plan, dict):
        raise ForgeError(
            f"`plan` must be the proposed floor-plan object, not a "
            f"{type(plan).__name__}."
        )
    if against is None and project is None:
        raise ForgeError(
            "floorplan_diff compares two plans, so it needs the other one: pass "
            "`against` (a plan object) or `project` (whose saved "
            "design/floorplan.json is the before)."
        )
    if against is not None and project is not None:
        raise ForgeError(
            "Pass `against` OR `project`, not both — they are two different "
            "answers to 'what is this being compared with', and guessing which "
            "one you meant is how a diff ends up measured against the wrong "
            "before."
        )
    if against is not None:
        if not isinstance(against, dict):
            raise ForgeError(
                f"`against` must be a floor-plan object, not a "
                f"{type(against).__name__}."
            )
        old, old_source = against, "the plan you passed as `against`"
    else:
        _slug, path, old = util.read_floorplan(project)
        old_source = str(path)

    return fmt_floorplan_diff(
        floorplan.diff(old, plan),
        old_source=old_source,
        new_source="the proposed plan",
    )


@app.tool()
def floorplan_reconcile(
    project: str,
    apply: bool = False,
    collection: str = "Floorplan",
    floor: bool = True,
    confirm_deletions: bool = False,
) -> str:
    """They moved it, resized it or deleted it BY HAND — read that back.

    The return channel, and the answer to *"I should be able to manually edit
    and forge should be aware of my changes."* `floorplan_build` already refuses
    to clobber a hand-edited object, but that protection is one-way: the plan
    never finds out, so it keeps proposing the old layout and a wall they
    deleted on purpose comes back on the next build. This measures what they
    actually did — in millimetres, off each object's own transform and mesh —
    and folds it into the plan, so the plan stays the model.

    - `project`: whose `design/floorplan.json` is being measured against, and
      where the absorbed plan is saved back.
    - `apply`: **false** (the default) measures and reports, changing nothing.
      **true** writes the measurements into the plan and saves it, then quotes
      the diff. Nothing is rebuilt either way — the next build finds those ids
      unchanged, which is the point.
    - `collection` / `floor`: the same two the level was built with. Pass the
      same `floor` you built with: with `floor=false` no slab was ever built, and
      a missing slab is not a deleted room.
    - `confirm_deletions`: **false** (the default) absorbs a deletion the way it
      absorbs a move — they deleted the wall because there is no wall there, and
      a plan that keeps re-proposing it is a plan arguing with them. Pass true to
      have deletions listed for a human yes instead.

    **What cannot be absorbed is said, not approximated.** A tilted or sheared
    object, a sculpted placeholder, one lifted off the floor: no field of
    `floorplan.json` can hold any of those, so each comes back under "cannot
    absorb" with the reason. A sculpted object is a PROMOTION — mark it
    `forge_fp_keep` and the plan keeps its slot's footprint. An `FP:` box Forge
    never built is reported as a candidate and never added on its own: a new
    fixture needs an id and a label from a person.

    Reads the scene and never writes to it (the add-on command is read-only).
    Needs Blender running with the Forge add-on server started.
    """
    slug, path, saved = util.read_floorplan(project)
    resolved = floorplan.resolve(saved)
    name = (collection or "").strip() or "Floorplan"
    result = blender_client.send_command(
        "reconcile_floorplan",
        floorplan.reconcile_params(resolved, name, floor),
    )

    if not apply:
        return floorplan.fmt_reconcile_report(result, source=str(path))

    absorbed = floorplan.absorb(saved, result,
                                confirm_deletions=bool(confirm_deletions))
    saved_path: Optional[Path] = None
    overwritten = False
    quote: Optional[str] = None
    if absorbed.get("applied"):
        saved_path, overwritten = util.write_floorplan(slug, absorbed["plan"])
        quote = fmt_floorplan_diff(
            floorplan.diff(saved, absorbed["plan"]),
            old_source="the plan as it was",
            new_source="the plan with your scene edits in it",
        )
    return floorplan.fmt_reconcile_report(
        result, source=str(path), absorbed=absorbed, saved=saved_path,
        overwritten=overwritten, diff_quote=quote,
    )


def _sync_scene_first(
    plan: Optional[Dict[str, Any]],
    slug: Optional[str],
    project: Optional[str],
    collection: str,
    floor: bool,
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """Absorb the scene's hand edits BEFORE a build. `(plan|None, lines)`.

    The ordering is the whole guarantee: measure what is actually in the
    collection, fold it into the plan, and only then build. A build that edits
    first and measures never cannot help resurrecting the wall the artist
    deleted — which is exactly what happened to `FP:wall-living-kitchen`, twice.

    A plan the CALLER is editing on this same turn wins over the scene for the
    ids it touches: that edit is the later intent, and absorbing both would be
    two answers to one question. Everything else in the scene is absorbed.

    Anything that stops the measurement — an add-on too old to know the command,
    a collection with nothing in it, a project with no saved plan — is a line in
    the report and never a refusal. The build is what the artist asked for.
    """
    if slug is None:
        return None, []
    try:
        _slug, path, saved = util.read_floorplan(project)
        resolved = floorplan.resolve(saved)
        result = blender_client.send_command(
            "reconcile_floorplan",
            floorplan.reconcile_params(resolved, collection, floor),
        )
        protect = floorplan.touched_ids(saved, plan) if plan is not None else []
        target = plan if plan is not None else saved
        absorbed = floorplan.absorb(target, result, skip_ids=protect)
    except ForgeError as exc:
        return None, [
            f"Could not read your scene edits back first ({exc}). Building the "
            f"plan as it stands — if you have moved or deleted anything in the "
            f"viewport, say so and I will run floorplan_reconcile."
        ]
    if not absorbed.get("applied"):
        return None, []
    saved_path, _overwritten = util.write_floorplan(slug, absorbed["plan"])
    return absorbed["plan"], floorplan.fmt_sync_lines(result, absorbed, saved_path)


@app.tool()
def floorplan_build(
    plan: Optional[Dict[str, Any]] = None,
    project: Optional[str] = None,
    collection: str = "Floorplan",
    mode: Literal["update", "rebuild"] = "update",
    floor: bool = True,
    sync: bool = True,
) -> str:
    """Materialise a floor plan as a greybox level in Blender — incrementally.

    Walls extruded on their centrelines, openings cut by construction (a pier
    between them, a header over, a sill under a window), each room an optional
    slab, each label a named box at its real footprint. **Only after the artist
    has approved `design/floorplan.svg`** — the echo-back gate is the whole
    safety story of this phase: they correct the diagram, never the mesh.

    - `plan` / `project`: the plan object, or the project whose saved plan to
      build. Both is fine (build this proposal into that project's level).
    - `mode`: **"update"** (the default) diffs the plan against what is already
      in the collection by id and touches only what changed — an unchanged entry
      keeps the same object, the same mesh datablock and the same transform.
      **"rebuild"** throws the greybox away and builds it again, so hand edits
      to it are gone; that is what the mode is for, and ask before using it.
    - `collection`: which collection the level lives in ("Floorplan"). Only
      `FP:`-prefixed objects *inside it* are ever touched.
    - `floor`: whether rooms get slabs, for the build as a whole. A single
      room opts out on its own with `"floor": false` on its plan entry — the
      add-on honours it and reports the slab it skipped.
    - `sync`: **true** (the default, with a `project`) reads the artist's hand
      edits out of the collection and folds them into the plan BEFORE building —
      so a build cannot resurrect a wall they deleted or snap back something
      they moved. The report leads with what was absorbed. Turn it off only to
      build a plan deliberately against a scene you know is stale.

    **Nothing hand-edited is clobbered.** An `FP:` object whose mesh no longer
    matches what its entry would build — a different vertex count, dimensions
    off by more than 0.5 mm — or that carries `forge_fp_keep` is KEPT and
    reported with a sentence naming it, in every mode. That is promotion: a
    placeholder is a component SLOT, elaborating it is one-way, and the level
    never rebuilds around it.

    Needs Blender running with the Forge add-on server started. The plan is
    validated and resolved HERE first, so a refusal costs no round trip.
    """
    slug, source_plan, _source = _plan_source(plan, project, tool="floorplan_build")

    name = (collection or "").strip() or "Floorplan"

    # Absorb the scene FIRST, then build. Not a nicety: with the edit applied
    # first, a build has no way of knowing that the wall it is about to create
    # was deleted on purpose.
    lead: List[str] = []
    if sync:
        merged, lead = _sync_scene_first(plan, slug, project, name, bool(floor))
        if merged is not None:
            source_plan = merged

    # Resolved on this side: what crosses the socket is a plan with every number
    # explicit, so the add-on cannot silently substitute a default of its own.
    resolved = floorplan.resolve(source_plan)

    result = blender_client.send_command("build_floorplan", {
        "plan": resolved,
        "collection": name,
        "mode": mode,
        "floor": bool(floor),
    })

    tally = floorplan.counts(resolved)
    summary = (
        f"{mode} mode, {fmt_plan_counts(tally)}"
        + (f", {fmt_number(result.get('seconds'), 2)} s"
           if result.get("seconds") is not None else "")
    )
    return "\n".join(lead + [fmt_floorplan_build_report(result, summary)])


# ---------------------------------------------------------------------------
# Flows (Phase 6b) — saved sequences that replay with no model in the loop
# ---------------------------------------------------------------------------


@app.tool()
def flow_list() -> str:
    """What has already been saved as a repeatable sequence? Check this FIRST.

    A flow is a job someone worked out once, written down: a named, parameterised
    list of Forge operations that replays exactly the same way every time, with
    no model deciding anything. Before improvising a multi-step job, look here —
    running a matching flow is faster, free and deterministic, and it is the same
    button the artist has in their panel.

    Reads `flows/*.json` off disk, so it answers whether or not Blender is
    running. Each entry lists the flow's description, its parameters with their
    defaults, and its steps in order.
    """
    root, flows = read_flow_files()
    return fmt_flow_list(root, flows)


@app.tool()
def flow_run(name: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Replay a saved flow, optionally with different parameter values.

    The steps run in order and stop at the first failure, which names the step
    that broke. Blender steps execute inside Blender; geometry-service steps go
    out over HTTP; a step can feed the next one its result. No model is in the
    loop — this is the same sequence every time.

    `params` overrides the flow's declared defaults: `{"wedges": 6}`. Only
    declared parameters are accepted, so a misspelled name is an error naming
    what the flow actually takes rather than a silently ignored argument.

    Needs Blender running with the Forge add-on server started (that is where
    flows execute), and the geometry service for any service step.
    """
    slug = flow_slug(name)
    request: Dict[str, Any] = {"name": slug}
    if params:
        if not isinstance(params, dict):
            raise ForgeError("`params` must be an object of {name: value}.")
        request["params"] = params
    result = blender_client.send_command(
        "flow_run", request, read_timeout=config.FLOW_RUN_TIMEOUT
    )
    return fmt_flow_run_report(slug, result)


@app.tool()
def flow_save(
    name: str,
    description: str,
    steps: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    overwrite: bool = False,
) -> str:
    """Save a multi-step job you just finished as a flow the artist can re-run.

    Do this after any repeatable sequence of two or more operations — segment
    and load, retopo and unwrap, check and export. The artist then gets a button
    (N -> Forge tab -> Flows) that does the same thing with no turn spent, and
    you get something to find with `flow_list` next time instead of working it
    out again.

    - `name` is plain words ("segment into 4"); it becomes
      `flows/segment-into-4.json`. Anything path-shaped is refused — nothing is
      ever written elsewhere.
    - `description` is one sentence for a human; it is the tooltip in the panel.
    - `steps` is the sequence: `{"kind": "blender"|"service", "op": <socket
      command | endpoint>, "args": {...}, "label": "what this step does"}`. Every
      `op` is checked against the real command/endpoint set before anything is
      written. Label every step — the labels are what the artist reads, and what
      a failure is reported against.
    - `params` declares what can vary: `{"wedges": {"value": 4, "unit": "count",
      "description": "..."}}`. Use `"{{wedges}}"` in an argument to fill it in;
      a placeholder that is the whole value keeps its type (the number 4). An
      argument may also read an earlier step with `"{{steps.0.result.segments}}"`
      (dotted lookup only).
    - In a service step, `script_path` and `printer_path` are read from disk for
      you and become `script`/`printer`; leave them `""` to mean "whatever the
      PartForge panel is pointed at", which is what makes one flow serve every
      part.

    Never save a single-step flow — that is just the tool call. `overwrite=true`
    replaces an existing flow of the same name.
    """
    slug = flow_slug(name)
    doc = flow_document(name, slug, description, params, steps)
    path = flow_path(slug)

    existed = path.exists()
    if existed and not overwrite:
        raise ForgeError(
            f"{path} already exists. Run it with flow_run('{slug}'), or pass "
            "overwrite=true to replace it with this version. To save a different "
            "sequence, give it another name."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        raise ForgeError(f"Could not write {path}: {exc}") from exc

    return fmt_flow_saved(path, doc, overwritten=existed)


def main() -> None:
    """Entry point: serve MCP over stdio."""
    app.run()


if __name__ == "__main__":  # pragma: no cover
    main()
