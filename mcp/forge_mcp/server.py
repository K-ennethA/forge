"""Forge MCP server — one stdio tool surface over both Forge backends.

* Blender add-on socket (127.0.0.1:9876) — scene inspection, common mesh ops,
  mesh loading, STL export.
* Build123d geometry service (127.0.0.1:8765) — PartForge parametric parts.

Protocols and command names are fixed by docs/architecture.md; the tools here
are thin, well-labelled wrappers over them.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Union

from mcp.server.mcpserver import MCPServer

from . import __version__, blender_client, config, meshgen_client, service_client
from .errors import BackendError, BackendUnavailable, ForgeError
from .util import (
    GLTF_SUFFIXES,
    MOCAP_SUFFIXES,
    PLATFORM_TARGET_FACES,
    action_name_for_clip,
    action_names,
    derivative_objects,
    ensure_parent_dir,
    flow_document,
    flow_path,
    flow_slug,
    fmt_action_report,
    fmt_check_report,
    fmt_cloth_report,
    fmt_export_report,
    fmt_flow_list,
    fmt_flow_run_report,
    fmt_flow_saved,
    fmt_generate_report,
    fmt_joint,
    fmt_keyframe_report,
    fmt_manifest_report,
    fmt_meshgen_status,
    fmt_metarig_report,
    fmt_mode,
    fmt_model_check_report,
    fmt_model_segment_report,
    fmt_new_part_report,
    fmt_number,
    fmt_open_report,
    fmt_overrides,
    fmt_params,
    fmt_reference_report,
    fmt_retarget_report,
    fmt_retopo_report,
    fmt_rig_report,
    fmt_scene_info,
    fmt_segment_report,
    fmt_stats,
    fmt_submitted_report,
    fmt_tag_table,
    fmt_uv_report,
    fmt_vector,
    fmt_weights_report,
    fmt_written_files,
    generated_object_name,
    keys_frame_range,
    meshgen_image_path,
    next_rig_step,
    normalize_action_name,
    normalize_actions,
    normalize_bone_mapping,
    normalize_face_indices,
    normalize_keys,
    normalize_modules,
    normalize_retarget_scale,
    normalize_script_source,
    normalize_segment_mode,
    normalize_tag_list,
    normalize_tag_name,
    object_name_for_script,
    ok,
    plate_items_by_name,
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
- Print readiness is a pipeline: partforge_check first; if bed_fit fails it
  hands back a `mode` object — pass it verbatim to partforge_segment (planning,
  no meshes), partforge_load_segments (same, plus the pieces laid out in the
  viewport) or partforge_export_segments (files for the slicer).
- A model the artist DOWNLOADED or imported (an STL/OBJ off the internet, their
  own sculpt) has no PARAMS script, so the partforge_* tools cannot touch it:
  check it with check_model and cut it with segment_model, which work off the
  Blender object. Both refuse a mesh that is not watertight — repair that with
  remesh(mode="voxel") first, then ask again.
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
- generate_3d turns a picture into an actual mesh (meshgen, ~5 minutes, one job
  at a time) and is the right first offer for ORGANIC, stylised, one-off shapes
  — a creature, a bust, an ornament. Anything functional or dimensioned stays
  parametric: a generated mesh has no crisp faces and no exact millimetres. It
  always arrives voxel-repaired and print-checked; say the wait out loud before
  starting, and meshgen_status reports the stage while it runs.
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

    try:
        loaded = blender_client.send_command(
            "load_mesh",
            {
                "name": object_name,
                "vertices": vertices,
                "faces": faces,
                "replace": True,
            },
        )
    except BackendUnavailable as exc:
        lines.append(f"  NOT loaded into Blender — {exc}")
    except ForgeError as exc:
        lines.append(f"  Blender refused the mesh — {exc}")
    else:
        lines.append(
            f"  Loaded into Blender as '{loaded.get('object', object_name)}' "
            f"({loaded.get('vertex_count', '?')} verts, "
            f"{loaded.get('face_count', '?')} faces), mesh replaced in place."
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
                json.dumps(spec_document(name, slug, params), indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            spec_created = True
        except OSError as exc:  # a missing spec must not lose the script
            return fmt_new_part_report(
                name=name, slug=slug, script=script, params=params,
                created=not existed, spec=None, spec_created=False,
            ) + f"\n  (spec.json could not be written: {exc})"

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
    partforge_export_segments as `mode`.

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
    object straight to segment_model as `mode`. And the same two caveats as
    partforge_check apply: min_wall is approximate inward ray casting ("look
    here", not an exact dimension), and overhangs warn rather than fail.
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
    backend: Optional[str] = None,
    wait: bool = True,
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
      than the exporter's `Mesh_0`.
    - `backend`: omit for the service's default (trellis2). "pixal3d" is the
      other installed model; anything else is refused with the list.
    - `wait`: true (default) blocks until the mesh is in the scene. false hands
      back the job id immediately — then poll meshgen_status(job_id).

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

    submitted = meshgen_client.generate3d(str(image), backend=chosen)
    job_id = str(submitted.get("job_id") or "").strip()
    if not job_id:
        raise BackendError(
            "meshgen accepted the job but returned no job_id, so there is "
            f"nothing to follow: {submitted}"
        )

    if not wait:
        return fmt_submitted_report(image, submitted)

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
        return fmt_generate_report(image, submitted, job, stages)

    mesh_path = str(job.get("mesh_path") or "")
    if not mesh_path:
        return fmt_generate_report(
            image, submitted, job, stages,
            problems=["meshgen finished but named no file, so nothing could be imported."],
        )

    imported, problems = _import_generated(mesh_path, generated_object_name(image))
    check = None
    if imported:
        check, check_problems = _check_generated(imported.get("object"))
        problems.extend(check_problems)
    return fmt_generate_report(image, submitted, job, stages, imported, check, problems)


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

    Reports the metarig's name, its bone count and which bones each tag drove,
    and relays the add-on's warnings (a missing landmark tag shows up here, not
    later as a bone in the wrong place). Nothing is skinned yet — look at the
    placement in Blender before running rigforge_generate_rig.
    """
    params = _target(object)
    params["archetype"] = archetype
    if modules is not None:
        params["modules"] = normalize_modules(modules)

    result = blender_client.send_command("rigforge_metarig", params)
    summary = f"archetype {archetype}"
    if modules is not None:
        summary += f", {len(params['modules'])} extra module(s)"
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
