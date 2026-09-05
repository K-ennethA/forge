"""Forge MCP server — one stdio tool surface over both Forge backends.

* Blender add-on socket (127.0.0.1:9876) — scene inspection, common mesh ops,
  mesh loading, STL export.
* Build123d geometry service (127.0.0.1:8765) — PartForge parametric parts.

Protocols and command names are fixed by docs/architecture.md; the tools here
are thin, well-labelled wrappers over them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from mcp.server.mcpserver import MCPServer

from . import __version__, blender_client, config, service_client
from .errors import BackendUnavailable, ForgeError
from .util import (
    ensure_parent_dir,
    fmt_check_report,
    fmt_joint,
    fmt_mode,
    fmt_number,
    fmt_overrides,
    fmt_params,
    fmt_scene_info,
    fmt_segment_report,
    fmt_stats,
    fmt_written_files,
    normalize_segment_mode,
    object_name_for_script,
    ok,
    plate_items_by_name,
    read_printer,
    read_script,
    resolve_path,
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
- Print readiness is a pipeline: partforge_check first; if bed_fit fails it
  hands back a `mode` object — pass it verbatim to partforge_segment (planning,
  no meshes), partforge_load_segments (same, plus the pieces laid out in the
  viewport) or partforge_export_segments (files for the slicer).
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
    """Check whether both Forge backends are up, in one call.

    Reports the Blender add-on socket (127.0.0.1:9876) and the Build123d
    geometry service (127.0.0.1:8765) with the Blender/build123d versions when
    reachable. Never fails — use it to diagnose any "backend is not running"
    error, or before starting a session.
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


def main() -> None:
    """Entry point: serve MCP over stdio."""
    app.run()


if __name__ == "__main__":  # pragma: no cover
    main()
