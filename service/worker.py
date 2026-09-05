"""The geometry worker: the child process that actually touches build123d.

Two ways to run it:

* ``python -m service.worker --serve`` -- the mode :class:`runner.WorkerPool`
  uses.  Reads one JSON envelope per line on stdin, writes the result to a file,
  and acknowledges on stdout.  build123d is imported once at startup so slider
  regeneration does not pay for it again.
* ``python -m service.worker <job.json> <out.json>`` -- one shot, for debugging
  a script outside the HTTP service.

Protocol (newline-delimited JSON on stdin/stdout)::

    parent -> child : {"job": "<path to job.json>", "out": "<path to out.json>"}
    child  -> parent: {"ok": true, "out": "<path>"}            (acknowledgement)

The real payload always travels through the out file, never the pipe: meshes
run to megabytes and a pipe that big invites deadlocks.  The out file is::

    {"ok": true,  "result": {...}, "stdout": "<anything the script printed>"}
    {"ok": false, "kind": "script"|"service", "error": "...", "traceback": "...",
     "stdout": "..."}

Anything the script prints is captured rather than allowed onto stdout -- a
stray ``print()`` in a generated script would otherwise desync the protocol.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, Mapping, Optional, Tuple

from . import params as params_module
from .checks import MeshGeometry, run_checks, suggest_segmentation
from .errors import ForgeError, ScriptError, ServiceError
from .export import (
    FORMATS,
    export_plate_3mf,
    export_shape,
    normalize_format,
    resolve_output_dir,
    safe_basename,
)
from .joints import resolve_joint
from .mesh_input import (
    check_triangle_ceiling,
    load_mesh_input,
    require_watertight,
    sew_to_solid,
    sew_tolerance_for,
)
from .mold import build_mold, solid_volume
from .mold import normalize_options as normalize_mold_options
from .printer import (
    DEFAULT_PLATE_MARGIN_MM,
    DEFAULT_PLATE_SPACING_MM,
    normalize_printer,
)
from .runner import (
    DEFAULT_ANGULAR_TOLERANCE,
    DEFAULT_TOLERANCE_MM,
    compute_mesh_stats,
    compute_stats,
    normalize_build_result,
    tessellate_shape,
    weld_vertices,
)
from .segmenting import (
    drop_to_origin,
    min_area_orientation,
    normalize_mode,
    pack_plate,
    place_on_plate,
    resolve_auto_mode,
    rotated_bounds,
    segment_shape,
)

# --------------------------------------------------------------------------
# build123d warm import
# --------------------------------------------------------------------------

_B3D_VERSION: Optional[str] = None
_B3D_IMPORT_ERROR: Optional[str] = None


def warm_import() -> None:
    """Import build123d once, up front, and remember how it went.

    A failure is recorded rather than raised so ``/health`` can report a clear
    "build123d is not installed" instead of the worker dying on startup.
    """
    global _B3D_VERSION, _B3D_IMPORT_ERROR
    if _B3D_VERSION is not None or _B3D_IMPORT_ERROR is not None:
        return
    try:
        import build123d  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - a missing kernel is a normal state
        _B3D_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return

    version = getattr(build123d, "__version__", None)
    if not version:
        try:
            from importlib.metadata import version as _dist_version  # noqa: PLC0415

            version = _dist_version("build123d")
        except Exception:  # noqa: BLE001
            version = "unknown"
    _B3D_VERSION = str(version)


def _require_build123d() -> None:
    warm_import()
    if _B3D_IMPORT_ERROR is not None:
        raise ServiceError(
            "build123d is not importable in the geometry worker "
            f"({_B3D_IMPORT_ERROR}); install the service dependencies with "
            "`pip install -e .` inside service/"
        )


# --------------------------------------------------------------------------
# Job handlers
# --------------------------------------------------------------------------


def _tolerances(job: Mapping[str, Any]) -> Tuple[float, float]:
    tolerance = job.get("tolerance")
    angular = job.get("angular_tolerance")
    linear_value = float(tolerance) if tolerance else DEFAULT_TOLERANCE_MM
    angular_value = float(angular) if angular else DEFAULT_ANGULAR_TOLERANCE
    if linear_value <= 0:
        raise ScriptError("tolerance must be greater than zero")
    if angular_value <= 0:
        raise ScriptError("angular_tolerance must be greater than zero")
    return linear_value, angular_value


def _call_build(build_fn: Any, build_values: Dict[str, Any]) -> Any:
    """Call ``build(p)`` and turn anything it raises into a 400."""
    try:
        return build_fn(dict(build_values))
    except ForgeError:
        raise
    except BaseException as exc:  # noqa: BLE001 - report whatever the script did
        raise ScriptError(
            f"build(p) raised {type(exc).__name__}: {exc}", traceback.format_exc()
        ) from exc


def _build_shape(job: Mapping[str, Any]) -> Tuple[Dict[str, Any], Any, Dict[str, float]]:
    """Shared path for /generate and /export: resolve params, run build()."""
    _require_build123d()
    script = job.get("script")
    overrides = job.get("overrides") or {}

    started = time.perf_counter()
    schema, build_values, build_fn = params_module.load_script(script, overrides)
    resolved_at = time.perf_counter()

    shape = normalize_build_result(_call_build(build_fn, build_values))
    built_at = time.perf_counter()

    timings = {
        "resolve_ms": round((resolved_at - started) * 1000.0, 2),
        "build_ms": round((built_at - resolved_at) * 1000.0, 2),
    }
    return schema, shape, timings


def handle_health(_job: Mapping[str, Any]) -> Dict[str, Any]:
    warm_import()
    return {
        "build123d": _B3D_VERSION,
        "build123d_error": _B3D_IMPORT_ERROR,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "pid": os.getpid(),
    }


def handle_parse_params(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve the PARAMS schema without building any geometry."""
    script = job.get("script")
    namespace = params_module.exec_script(script)
    raw = params_module.extract_params(namespace)
    # build() must exist even though we do not call it -- catching a missing
    # build here is far friendlier than catching it on the first slider drag.
    params_module.extract_build(namespace)
    schema, _ = params_module.resolve(raw, job.get("overrides") or {})
    return {"params": schema}


def handle_generate(job: Mapping[str, Any]) -> Dict[str, Any]:
    linear, angular = _tolerances(job)
    schema, shape, timings = _build_shape(job)

    started = time.perf_counter()
    vertices, triangles = tessellate_shape(shape, linear, angular)
    vertices, triangles, degenerate = weld_vertices(vertices, triangles)
    timings["tessellate_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    if not triangles:
        raise ScriptError(
            "the generated shape produced no triangles; check that build(p) "
            "returns a solid with volume"
        )

    stats = compute_stats(shape, vertices, triangles, degenerate)
    return {
        "params": schema,
        "mesh": {"vertices": vertices, "faces": triangles},
        "stats": stats,
        "timings": timings,
    }


def handle_export(job: Mapping[str, Any]) -> Dict[str, Any]:
    linear, angular = _tolerances(job)
    _schema, shape, _timings = _build_shape(job)
    written = export_shape(
        shape,
        job.get("format"),
        job.get("path"),
        tolerance=linear,
        angular_tolerance=angular,
    )
    return {"path": written}


# --------------------------------------------------------------------------
# Phase 2: print readiness
# --------------------------------------------------------------------------


def _mesh_and_stats(
    shape: Any, linear: float, angular: float
) -> Tuple[list, list, Dict[str, Any]]:
    """Tessellate, weld and measure -- the same path ``/generate`` takes."""
    vertices, triangles = tessellate_shape(shape, linear, angular)
    vertices, triangles, degenerate = weld_vertices(vertices, triangles)
    if not triangles:
        raise ScriptError(
            "the shape produced no triangles; check that build(p) returns a solid "
            "with volume"
        )
    return vertices, triangles, compute_stats(shape, vertices, triangles, degenerate)


def _plate_options(job: Mapping[str, Any]) -> Tuple[float, float]:
    margin = job.get("plate_margin_mm")
    spacing = job.get("plate_spacing_mm")
    return (
        float(margin) if margin is not None else DEFAULT_PLATE_MARGIN_MM,
        float(spacing) if spacing is not None else DEFAULT_PLATE_SPACING_MM,
    )


def handle_check(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Run the printer-aware checks against the built solid."""
    linear, angular = _tolerances(job)
    printer = normalize_printer(job.get("printer"))
    margin, _spacing = _plate_options(job)

    schema, shape, timings = _build_shape(job)

    started = time.perf_counter()
    vertices, triangles, stats = _mesh_and_stats(shape, linear, angular)
    timings["tessellate_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    started = time.perf_counter()
    report = run_checks(
        vertices,
        triangles,
        stats,
        printer,
        margin_mm=margin,
        min_wall_probe_mm=job.get("min_wall_probe_mm"),
        max_wall_samples=int(job.get("max_wall_samples") or 4000),
    )
    timings["check_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    return {
        "overall": report["overall"],
        "checks": report["checks"],
        "params": schema,
        "printer": printer,
        "stats": stats,
        "timings": timings,
    }


def _segment_context(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Everything a segmenting job validates *before* it touches geometry.

    A bad joint type or an impossible mode should come back immediately, not
    after a minute of building or sewing.
    """
    linear, angular = _tolerances(job)
    printer = normalize_printer(job.get("printer"))
    margin, spacing = _plate_options(job)
    return {
        "linear": linear,
        "angular": angular,
        "printer": printer,
        "margin": margin,
        "spacing": spacing,
        "joint": resolve_joint(job.get("joint"), printer),
        "mode": normalize_mode(job.get("mode")),
    }


def _segment_common(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Build, cut, join and verify.  Shared by /segment and /export_segments."""
    context = _segment_context(job)
    linear, angular = context["linear"], context["angular"]

    schema, shape, timings = _build_shape(job)

    started = time.perf_counter()
    vertices, triangles, stats = _mesh_and_stats(shape, linear, angular)
    timings["tessellate_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    return _segment_tail(context, shape, vertices, triangles, stats, timings, schema)


def _segment_tail(
    context: Mapping[str, Any],
    shape: Any,
    vertices: list,
    triangles: list,
    stats: Dict[str, Any],
    timings: Dict[str, float],
    schema: Any,
) -> Dict[str, Any]:
    """Cut, join, verify and pack -- identical for a script and for a mesh.

    By the time a job reaches here the difference between "a PARAMS script built
    this solid" and "somebody downloaded this mesh and we sewed it" is gone: both
    are a build123d shape plus the triangles that describe it.
    """
    linear, angular = context["linear"], context["angular"]
    printer = context["printer"]
    margin, spacing = context["margin"], context["spacing"]
    joint_spec = context["joint"]
    mode = context["mode"]

    suggestion: Optional[Dict[str, Any]] = None
    if mode["kind"] == "auto":
        geometry = MeshGeometry(vertices, triangles)
        size = tuple(float(v) for v in stats["bounding_box_mm"])
        low = tuple(float(v) for v in stats["bounding_box_min_mm"])
        high = tuple(float(v) for v in stats["bounding_box_max_mm"])
        suggestion = suggest_segmentation(geometry, size, low, high, printer, margin)
        mode = resolve_auto_mode(suggestion)

    started = time.perf_counter()
    cut = segment_shape(shape, mode, joint_spec)
    timings["segment_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    started = time.perf_counter()
    segments = []
    for item in cut["items"]:
        piece_vertices, piece_triangles, piece_stats = _mesh_and_stats(
            item["solid"], linear, angular
        )
        if not piece_stats["watertight"]:
            # Contract: a segmenting operation that produces non-manifold output
            # is an error.  A slicer cannot do anything with it, so neither can we.
            raise ScriptError(
                f"{item['name']} came out non-manifold "
                f"(boundary edges {piece_stats['boundary_edges']}, non-manifold "
                f"{piece_stats['nonmanifold_edges']}, B-Rep valid "
                f"{piece_stats['solid_is_valid']}). The joint or the cut is degenerate "
                "at that face; try a smaller joint, a different joint type, or moving "
                "the cut."
            )
        # A wedge comes out of the cut wherever its arc sat, so its axis-aligned
        # footprint can be the whole outer diameter.  Spinning it about the
        # build axis is free -- overhangs and layer heights do not change -- and
        # is often what makes it fit the bed at all.  The mesh returned here is
        # *not* rotated: /segment's meshes stay assembly-accurate so a caller
        # can show the part coming apart.  Exports apply the spin.
        orient = min_area_orientation([(v[0], v[1]) for v in piece_vertices])
        width, depth = rotated_bounds(
            [(v[0], v[1]) for v in piece_vertices], orient
        )
        height = float(piece_stats["bounding_box_mm"][2])
        segments.append(
            {
                "name": item["name"],
                "kind": item["kind"],
                "solid": item["solid"],
                "mesh": {"vertices": piece_vertices, "faces": piece_triangles},
                "stats": piece_stats,
                "orient_deg": orient,
                "oriented_bbox_mm": [
                    round(width, 4),
                    round(depth, 4),
                    round(height, 4),
                ],
            }
        )
    timings["verify_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    plate = pack_plate(
        [
            {
                "name": s["name"],
                "size_mm": s["oriented_bbox_mm"],
                "orient_deg": s["orient_deg"],
            }
            for s in segments
        ],
        printer,
        margin_mm=margin,
        spacing_mm=spacing,
    )

    return {
        "params": schema,
        "printer": printer,
        "mode": mode,
        "suggestion": suggestion,
        "joint": joint_spec,
        "cuts": cut["cuts"],
        "segments": segments,
        "plate": plate,
        "stats": stats,
        "timings": timings,
        "tolerances": (linear, angular),
    }


def handle_segment(job: Mapping[str, Any]) -> Dict[str, Any]:
    return _segment_payload(job, _segment_common(job))


def _segment_payload(
    job: Mapping[str, Any], result: Mapping[str, Any]
) -> Dict[str, Any]:
    include_mesh = job.get("include_mesh")
    include_mesh = True if include_mesh is None else bool(include_mesh)

    segments = []
    for segment in result["segments"]:
        entry: Dict[str, Any] = {
            "name": segment["name"],
            "kind": segment["kind"],
            "stats": segment["stats"],
            "orient_deg": segment["orient_deg"],
            "oriented_bbox_mm": segment["oriented_bbox_mm"],
        }
        if include_mesh:
            entry["mesh"] = segment["mesh"]
        segments.append(entry)

    payload = {
        "params": result["params"],
        "printer": result["printer"],
        "mode": result["mode"],
        "joint": result["joint"],
        "cuts": result["cuts"],
        "segments": segments,
        "plate": result["plate"],
        "stats": result["stats"],
        "timings": result["timings"],
    }
    if result["suggestion"] is not None:
        payload["suggestion"] = result["suggestion"]
    if result.get("mesh_input") is not None:
        payload["mesh_input"] = result["mesh_input"]
        payload["sewing"] = result["sewing"]
    return payload


def handle_export_segments(job: Mapping[str, Any]) -> Dict[str, Any]:
    return _export_segments_payload(job, _segment_common(job))


def _export_segments_payload(
    job: Mapping[str, Any], result: Dict[str, Any]
) -> Dict[str, Any]:
    linear, angular = result["tolerances"]

    directory = resolve_output_dir(job.get("directory"))
    # A downloaded model has no script name to borrow, so its files are "model_*"
    # rather than "part_*" unless the caller says otherwise.
    default_name = "model" if result.get("mesh_input") is not None else "part"
    basename = safe_basename(job.get("basename"), default=default_name)
    fmt = normalize_format(job.get("format") or "stl")

    started = time.perf_counter()
    files = []
    for segment in result["segments"]:
        # Each file is written on its own, sitting on Z=0 and centred in XY, so
        # a slicer opening one segment does not have to hunt for it.
        target = directory / f"{basename}_{segment['name']}{FORMATS[fmt]}"
        written = export_shape(
            drop_to_origin(segment["solid"], segment["orient_deg"]),
            fmt,
            str(target),
            tolerance=linear,
            angular_tolerance=angular,
        )
        files.append(
            {
                "name": segment["name"],
                "kind": segment["kind"],
                "format": fmt,
                "path": written,
                "stats": segment["stats"],
                "orient_deg": segment["orient_deg"],
            }
        )

    placement_by_name = {p["name"]: p for p in result["plate"]["items"]}
    plate_shapes = [
        (
            segment["name"],
            place_on_plate(segment["solid"], placement_by_name[segment["name"]]),
        )
        for segment in result["segments"]
    ]
    plate_path = export_plate_3mf(
        plate_shapes,
        str(directory / f"{basename}_plate.3mf"),
        tolerance=linear,
        angular_tolerance=angular,
    )
    result["timings"]["export_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    payload = {
        "params": result["params"],
        "printer": result["printer"],
        "mode": result["mode"],
        "joint": result["joint"],
        "cuts": result["cuts"],
        "directory": str(directory),
        "files": files,
        "plate": {**result["plate"], "path": plate_path},
        "plate_path": plate_path,
        "timings": result["timings"],
    }
    if result["suggestion"] is not None:
        payload["suggestion"] = result["suggestion"]
    if result.get("mesh_input") is not None:
        payload["mesh_input"] = result["mesh_input"]
        payload["sewing"] = result["sewing"]
    return payload


# --------------------------------------------------------------------------
# Phase 6d: mesh input -- "fix this downloaded model"
# --------------------------------------------------------------------------


def _load_mesh(job: Mapping[str, Any]) -> Tuple[list, list, Dict[str, Any], Dict[str, Any]]:
    """Read the caller's mesh and measure it the way ``/generate`` measures one.

    Returns ``(vertices, triangles, stats, info)``.  ``stats`` is the same block
    every other endpoint returns, except that ``solid_is_valid`` is ``null``:
    there is no B-Rep behind a mesh to ask OpenCascade about.
    """
    loaded = load_mesh_input(job)
    vertices = loaded["vertices"]
    triangles = loaded["faces"]
    info = loaded["info"]
    stats = compute_mesh_stats(vertices, triangles, info["degenerate_faces_dropped"])
    return vertices, triangles, stats, info


def handle_check_mesh(job: Mapping[str, Any]) -> Dict[str, Any]:
    """``/check``, but the geometry arrived as triangles instead of a script.

    The four checks were always mesh checks -- bed fit, wall probing, overhang
    angles and manifold analysis all read triangles -- so this is the same
    :func:`checks.run_checks` call, not a parallel implementation.
    """
    printer = normalize_printer(job.get("printer"))
    margin, _spacing = _plate_options(job)

    started = time.perf_counter()
    vertices, triangles, stats, info = _load_mesh(job)
    timings = {"load_ms": round((time.perf_counter() - started) * 1000.0, 2)}

    started = time.perf_counter()
    report = run_checks(
        vertices,
        triangles,
        stats,
        printer,
        margin_mm=margin,
        min_wall_probe_mm=job.get("min_wall_probe_mm"),
        max_wall_samples=int(job.get("max_wall_samples") or 4000),
    )
    timings["check_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    return {
        "overall": report["overall"],
        "checks": report["checks"],
        # There is no PARAMS schema behind a downloaded model.  The key is kept
        # so a caller can render a /check and a /check_mesh response with one
        # code path.
        "params": None,
        "printer": printer,
        "stats": stats,
        "mesh_input": info,
        "timings": timings,
    }


def _segment_mesh_common(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Load, refuse or sew, then hand the solid to the ordinary segmenting path."""
    context = _segment_context(job)

    started = time.perf_counter()
    vertices, triangles, stats, info = _load_mesh(job)
    timings = {"load_ms": round((time.perf_counter() - started) * 1000.0, 2)}

    # Both refusals come before any OCC work: they are the two things that make
    # sewing pointless (a mesh with holes) or ruinously slow (a dense one).
    require_watertight(stats)
    info["triangle_limit"] = check_triangle_ceiling(
        len(triangles), job.get("tri_limit")
    )

    started = time.perf_counter()
    tolerance = sew_tolerance_for(vertices, job.get("sew_tolerance_mm"))
    shape, sewing = sew_to_solid(vertices, triangles, tolerance)
    timings["sew_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    # The sewn solid is re-tessellated by the ordinary path, so from here the
    # numbers describe the solid that will actually be cut, not the input file.
    result = _segment_tail(context, shape, vertices, triangles, stats, timings, None)
    result["mesh_input"] = info
    result["sewing"] = sewing
    return result


def handle_segment_mesh(job: Mapping[str, Any]) -> Dict[str, Any]:
    return _segment_payload(job, _segment_mesh_common(job))


def handle_export_segments_mesh(job: Mapping[str, Any]) -> Dict[str, Any]:
    return _export_segments_payload(job, _segment_mesh_common(job))


# --------------------------------------------------------------------------
# Phase 2: mold mode
# --------------------------------------------------------------------------


def _mold_common(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Build, split, draft, box and verify.  Shared by /mold and /export_mold."""
    linear, angular = _tolerances(job)
    printer = normalize_printer(job.get("printer"))
    margin, _spacing = _plate_options(job)
    options = normalize_mold_options(job, printer)

    schema, shape, timings = _build_shape(job)

    started = time.perf_counter()
    vertices, triangles, stats = _mesh_and_stats(shape, linear, angular)
    timings["tessellate_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    started = time.perf_counter()
    mold = build_mold(
        shape, vertices, triangles, stats, printer, options, margin_mm=margin
    )
    timings["mold_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    started = time.perf_counter()
    halves = []
    for half in mold["halves"]:
        piece_vertices, piece_triangles, piece_stats = _mesh_and_stats(
            half["solid"], linear, angular
        )
        if not piece_stats["watertight"]:
            # Same contract as /segment: a half a slicer cannot use is an error,
            # not a warning with a broken mesh attached.
            raise ScriptError(
                f"{half['name']} came out non-manifold (boundary edges "
                f"{piece_stats['boundary_edges']}, non-manifold "
                f"{piece_stats['nonmanifold_edges']}, B-Rep valid "
                f"{piece_stats['solid_is_valid']}). Something in the cavity, the "
                "spout or a registration key is degenerate; try a smaller "
                "draft_deg, a bigger shell_mm, or a different parting_z_mm."
            )
        halves.append(
            {
                "name": half["name"],
                "solid": half["solid"],
                "mesh": {"vertices": piece_vertices, "faces": piece_triangles},
                "stats": piece_stats,
                # Additive: what the keys, spout and vents did to the half is
                # only visible as a volume, and a caller checking that the mold
                # is really a mold should not have to re-integrate the mesh.
                "volume_mm3": round(solid_volume(half["solid"]), 4),
            }
        )
    timings["verify_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    return {
        "params": schema,
        "printer": printer,
        "options": options,
        "mold": mold,
        "halves": halves,
        "stats_part": stats,
        "timings": timings,
        "tolerances": (linear, angular),
    }


def _mold_payload(result: Mapping[str, Any]) -> Dict[str, Any]:
    """The report half of a /mold response, without the meshes or the solids."""
    mold = result["mold"]
    return {
        "parting_z_mm": mold["parting_z_mm"],
        "parting_source": mold["parting_source"],
        "parting_profile": mold["parting_profile"],
        "draft": mold["draft"],
        "box": mold["box"],
        "cavity": mold["cavity"],
        "spout": mold["spout"],
        "vents": mold["vents"],
        "registration_keys": mold["registration_keys"],
        "bed": mold["bed"],
        "printer": result["printer"],
        "params": result["params"],
        "options": result["options"],
        "stats_part": result["stats_part"],
        "timings": result["timings"],
    }


def handle_mold(job: Mapping[str, Any]) -> Dict[str, Any]:
    result = _mold_common(job)
    include_mesh = job.get("include_mesh")
    include_mesh = True if include_mesh is None else bool(include_mesh)

    halves = []
    for half in result["halves"]:
        entry: Dict[str, Any] = {
            "name": half["name"],
            "stats": half["stats"],
            "volume_mm3": half["volume_mm3"],
        }
        if include_mesh:
            entry["mesh"] = half["mesh"]
        halves.append(entry)

    return {"halves": halves, **_mold_payload(result)}


def handle_export_mold(job: Mapping[str, Any]) -> Dict[str, Any]:
    result = _mold_common(job)
    linear, angular = result["tolerances"]

    directory = resolve_output_dir(job.get("directory"))
    basename = safe_basename(job.get("basename"), default="mold")
    fmt = normalize_format(job.get("format") or "stl")

    started = time.perf_counter()
    files = []
    for half in result["halves"]:
        target = directory / f"{basename}_{half['name']}{FORMATS[fmt]}"
        written = export_shape(
            # Centred in XY and sitting on Z=0, the same way a segment is
            # written, so a slicer opening one half does not have to hunt for it.
            drop_to_origin(half["solid"]),
            fmt,
            str(target),
            tolerance=linear,
            angular_tolerance=angular,
        )
        files.append(
            {
                "name": half["name"],
                "format": fmt,
                "path": written,
                "stats": half["stats"],
            }
        )
    result["timings"]["export_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    return {
        "directory": str(directory),
        "files": files,
        "halves": [
            {
                "name": half["name"],
                "stats": half["stats"],
                "volume_mm3": half["volume_mm3"],
            }
            for half in result["halves"]
        ],
        **_mold_payload(result),
    }


HANDLERS = {
    "health": handle_health,
    "parse_params": handle_parse_params,
    "generate": handle_generate,
    "export": handle_export,
    "check": handle_check,
    "segment": handle_segment,
    "export_segments": handle_export_segments,
    "check_mesh": handle_check_mesh,
    "segment_mesh": handle_segment_mesh,
    "export_segments_mesh": handle_export_segments_mesh,
    "mold": handle_mold,
    "export_mold": handle_export_mold,
}


def run_job(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Dispatch one job.  Raises :class:`ForgeError` subclasses on failure."""
    kind = job.get("kind")
    handler = HANDLERS.get(kind)
    if handler is None:
        raise ServiceError(
            f"unknown job kind {kind!r}; expected one of {', '.join(sorted(HANDLERS))}"
        )
    return handler(job)


def execute(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Run a job and wrap the outcome in the out-file envelope."""
    captured = io.StringIO()
    try:
        # Scripts print.  Keep every byte of it off the protocol stream.
        with contextlib.redirect_stdout(captured):
            result = run_job(job)
        payload: Dict[str, Any] = {"ok": True, "result": result}
    except ForgeError as exc:
        payload = {
            "ok": False,
            "kind": "service" if isinstance(exc, ServiceError) else "script",
            "error": exc.message,
            "traceback": exc.traceback_text,
        }
    except BaseException as exc:  # noqa: BLE001 - never let the worker die
        payload = {
            "ok": False,
            "kind": "service",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    text = captured.getvalue()
    if text:
        payload["stdout"] = text[-20000:]
    return payload


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def _write_out(path: str, payload: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def serve() -> int:
    """Long-lived mode: one job per stdin line until the pipe closes."""
    warm_import()

    stdout = sys.stdout
    with contextlib.suppress(Exception):
        stdout.reconfigure(encoding="utf-8", line_buffering=True)  # type: ignore[union-attr]

    while True:
        try:
            line = sys.stdin.readline()
        except (KeyboardInterrupt, EOFError):
            return 0
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue

        out_path: Optional[str] = None
        try:
            envelope = json.loads(line)
            out_path = envelope["out"]
            with open(envelope["job"], "r", encoding="utf-8") as handle:
                job = json.load(handle)
            payload = execute(job)
        except BaseException as exc:  # noqa: BLE001 - protocol errors included
            payload = {
                "ok": False,
                "kind": "service",
                "error": f"worker could not read its job: {type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

        if out_path:
            try:
                _write_out(out_path, payload)
            except Exception:  # noqa: BLE001 - the parent reports the missing file
                pass

        # Acknowledge exactly one line, whatever happened, so the parent never
        # waits out its full timeout on a failure it could have reported now.
        stdout.write(json.dumps({"ok": True, "out": out_path}) + "\n")
        stdout.flush()


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "--serve":
        return serve()
    if len(args) != 2:
        sys.stderr.write(
            "usage: python -m service.worker --serve\n"
            "       python -m service.worker <job.json> <out.json>\n"
        )
        return 2

    job_path, out_path = args
    warm_import()
    with open(job_path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    payload = execute(job)
    _write_out(out_path, payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
