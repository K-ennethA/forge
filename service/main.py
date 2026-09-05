"""FastAPI application and runner for the Forge geometry service.

Implements the HTTP API from ``docs/architecture.md`` on 127.0.0.1:8765::

    GET  /health          -> {"status": "ok", "build123d": "<version>"}
    POST /parse_params    -> {"params": <resolved schema>}
    POST /generate        -> {"params": ..., "mesh": {...}, "stats": {...}}
    POST /export          -> {"path": "<absolute path written>"}

Phase 2, print readiness::

    POST /check           -> {"overall": ..., "checks": [bed_fit, min_wall,
                              overhangs, watertight]}
    POST /segment         -> {"mode", "joint", "cuts", "segments": [...],
                              "plate": {...}}
    POST /export_segments -> {"files": [one per segment], "plate_path": "<3MF>"}
    POST /mold            -> {"halves": [mold_top, mold_bottom], "parting_z_mm",
                              "spout", "vents", "registration_keys"}
    POST /export_mold     -> {"files": [one per half]}
    POST /slice           -> {"output", "stdout_tail", "duration_ms"}

Phase 6d, mesh input -- the same answers for a model somebody downloaded::

    POST /check_mesh            -> the /check response, from triangles
    POST /segment_mesh          -> the /segment response, from triangles
    POST /export_segments_mesh  -> the /export_segments response, from triangles

Error contract: HTTP 400 with ``{"error", "traceback"}`` for script and
parameter failures, HTTP 500 for service bugs.

Run it with::

    python -m service.main            # from the repo root
    forge-service                     # after `pip install -e .` in service/

The service binds loopback only.  It has no authentication because it has no
network surface: it is a local kernel for Blender and the MCP server, and every
script it runs is one the user's own Claude session wrote.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

# Allow `python main.py` in addition to `python -m service.main` by giving the
# module its package context back before the relative imports below run.
if __package__ in (None, ""):  # pragma: no cover - only on direct execution
    _HERE = Path(__file__).resolve().parent
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))
    __package__ = _HERE.name

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import __version__  # noqa: E402
from .errors import ForgeError  # noqa: E402
from .export import normalize_format, resolve_output_path  # noqa: E402
from .runner import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    run_check,
    run_check_mesh,
    run_export,
    run_export_mold,
    run_export_segments,
    run_export_segments_mesh,
    run_generate,
    run_health,
    run_mold,
    run_parse_params,
    run_segment,
    run_segment_mesh,
    shutdown_pool,
)
from .slicer import health_detection, run_slice  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: Only loopback names may be bound.  The service runs unauthenticated scripts;
#: exposing it on a LAN address would be a remote code execution hole.
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

#: /health may have to wait out a cold build123d import in the worker.
HEALTH_TIMEOUT_S = 120.0


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class ParseParamsRequest(BaseModel):
    script: str = Field(..., description="PartForge script source")
    # Not part of the contract, but harmless and handy: lets a caller ask what
    # the schema looks like with a given set of overrides already applied.
    overrides: Dict[str, Any] = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    script: str = Field(..., description="PartForge script source")
    overrides: Dict[str, Any] = Field(default_factory=dict)
    # Extensions beyond the contract, both optional: tessellation quality knobs.
    tolerance: Optional[float] = Field(
        default=None, description="Linear deflection in mm (default 0.05)"
    )
    angular_tolerance: Optional[float] = Field(
        default=None, description="Angular deflection in radians (default 0.2)"
    )


class ExportRequest(BaseModel):
    script: str = Field(..., description="PartForge script source")
    overrides: Dict[str, Any] = Field(default_factory=dict)
    format: str = Field(..., description="stl | step | 3mf")
    path: str = Field(..., description="Absolute output file path")
    tolerance: Optional[float] = None
    angular_tolerance: Optional[float] = None


class CheckRequest(BaseModel):
    script: str = Field(..., description="PartForge script source")
    overrides: Dict[str, Any] = Field(default_factory=dict)
    printer: Optional[Dict[str, Any]] = Field(
        default=None,
        description="printer.json object; missing keys fall back to the built-in "
        "Elegoo Centauri Carbon profile",
    )
    tolerance: Optional[float] = None
    angular_tolerance: Optional[float] = None
    plate_margin_mm: Optional[float] = Field(
        default=None, description="Bed edge margin used by bed_fit (default 5 mm)"
    )
    min_wall_probe_mm: Optional[float] = Field(
        default=None,
        description="How far the wall probe looks (default 4x min_wall_thickness)",
    )
    max_wall_samples: Optional[int] = Field(
        default=None, description="Facet sample ceiling for the wall probe"
    )


class SegmentRequest(BaseModel):
    script: str = Field(..., description="PartForge script source")
    overrides: Dict[str, Any] = Field(default_factory=dict)
    printer: Optional[Dict[str, Any]] = None
    joint: Optional[Dict[str, Any]] = Field(
        default=None,
        description='{"type": "dovetail"|"pin"|"magnet"|"none", "tolerance": mm, ...}',
    )
    mode: Any = Field(
        default="auto",
        description='"auto" | {"radial": N} | {"planar": [z_mm, ...]}',
    )
    include_mesh: bool = Field(
        default=True, description="Return each segment's triangles as well as its stats"
    )
    tolerance: Optional[float] = None
    angular_tolerance: Optional[float] = None
    plate_margin_mm: Optional[float] = None
    plate_spacing_mm: Optional[float] = None


class ExportSegmentsRequest(SegmentRequest):
    directory: str = Field(..., description="Absolute output directory")
    basename: Optional[str] = Field(
        default=None, description="File-name stem; defaults to 'part'"
    )
    format: str = Field(default="stl", description="Per-segment format: stl|step|3mf")


class MeshInputRequest(BaseModel):
    """The input half of every mesh endpoint: triangles, or a file to read them from.

    Exactly one of ``mesh`` and ``file_path`` is given.  Coordinates are
    millimetres, matching every other number in this service.
    """

    mesh: Optional[Dict[str, Any]] = Field(
        default=None,
        description='{"vertices": [[x, y, z] mm, ...], "faces": [[i, j, k], ...]}; '
        "faces with more than three corners are triangulated",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Absolute path to an .stl (binary or ASCII), .3mf or .obj",
    )
    weld_tolerance_mm: Optional[float] = Field(
        default=None,
        description="Merge vertices closer than this before anything else "
        "(default: scale-relative, capped at 0.001 mm)",
    )


class CheckMeshRequest(MeshInputRequest):
    printer: Optional[Dict[str, Any]] = Field(
        default=None,
        description="printer.json object; missing keys fall back to the built-in "
        "Elegoo Centauri Carbon profile",
    )
    plate_margin_mm: Optional[float] = None
    min_wall_probe_mm: Optional[float] = None
    max_wall_samples: Optional[int] = None


class SegmentMeshRequest(MeshInputRequest):
    printer: Optional[Dict[str, Any]] = None
    joint: Optional[Dict[str, Any]] = Field(
        default=None,
        description='{"type": "dovetail"|"pin"|"magnet"|"none", "tolerance": mm, ...}',
    )
    mode: Any = Field(
        default="auto",
        description='"auto" | {"radial": N} | {"planar": [z_mm, ...]}',
    )
    include_mesh: bool = True
    tolerance: Optional[float] = None
    angular_tolerance: Optional[float] = None
    plate_margin_mm: Optional[float] = None
    plate_spacing_mm: Optional[float] = None
    sew_tolerance_mm: Optional[float] = Field(
        default=None,
        description="Tolerance handed to OpenCascade's sewer (default: the weld "
        "tolerance, i.e. the mesh's own vertex agreement)",
    )
    tri_limit: Optional[int] = Field(
        default=None,
        description="Override the triangle ceiling for this request "
        "(default FORGE_MESH_TRI_LIMIT)",
    )


class ExportSegmentsMeshRequest(SegmentMeshRequest):
    directory: str = Field(..., description="Absolute output directory")
    basename: Optional[str] = Field(
        default=None, description="File-name stem; defaults to 'model'"
    )
    format: str = Field(default="stl", description="Per-segment format: stl|step|3mf")


class MoldRequest(BaseModel):
    """``/mold``: the same part script, but you get the negative.

    Everything except ``script`` has a default, so ``{"script": ...}`` alone
    produces a sensible two-piece mold: parting plane at the widest slice, 2
    degrees of draft, a 4 mm shell, four keys, a spout and automatic vents.
    """

    script: str = Field(..., description="PartForge script source")
    overrides: Dict[str, Any] = Field(default_factory=dict)
    printer: Optional[Dict[str, Any]] = None
    parting_z_mm: Any = Field(
        default="auto",
        description='Z height of the parting plane in mm, or "auto" for the '
        "part's widest horizontal cross-section",
    )
    draft_deg: Optional[float] = Field(
        default=None, description="Draft angle on near-vertical cavity walls (default 2)"
    )
    shell_mm: Optional[float] = Field(
        default=None, description="Mold box wall thickness per side (default 4)"
    )
    clearance_mm: Optional[float] = Field(
        default=None, description="Grow the cavity by this much all round (default 0)"
    )
    spout: Any = Field(
        default=None,
        description='{"diameter_mm": mm, "position": [x, y]} or false for no spout',
    )
    vents: Any = Field(default="auto", description='Number of vents, or "auto"')
    registration_keys: Optional[int] = Field(
        default=None, description="Keys around the parting face (default 4, 0 for none)"
    )
    include_mesh: bool = Field(
        default=True, description="Return each half's triangles as well as its stats"
    )
    tolerance: Optional[float] = None
    angular_tolerance: Optional[float] = None
    plate_margin_mm: Optional[float] = None

    def mold_options(self) -> Dict[str, Any]:
        """The mold-shaped fields, forwarded verbatim to the worker."""
        return {
            "parting_z_mm": self.parting_z_mm,
            "draft_deg": self.draft_deg,
            "shell_mm": self.shell_mm,
            "clearance_mm": self.clearance_mm,
            "spout": self.spout,
            "vents": self.vents,
            "registration_keys": self.registration_keys,
        }


class ExportMoldRequest(MoldRequest):
    directory: str = Field(..., description="Absolute output directory")
    basename: Optional[str] = Field(
        default=None, description="File-name stem; defaults to 'mold'"
    )
    format: str = Field(default="stl", description="Per-half format: stl|step|3mf")


class SliceRequest(BaseModel):
    """``/slice``: hand a file we exported to the slicer that is installed."""

    input: str = Field(..., description="Absolute path to an .stl / .3mf we exported")
    output: str = Field(..., description="Absolute .gcode or .3mf path to write")
    printer: Optional[Dict[str, Any]] = Field(
        default=None,
        description="printer.json object; its `slicer` key decides which install "
        "is probed first",
    )
    profile: Any = Field(
        default=None,
        description="Absolute path to an OrcaSlicer .json profile, or a list of them "
        "(machine, then process)",
    )
    filaments: Any = Field(
        default=None, description="Absolute path(s) to filament .json profiles"
    )
    slicer_path: Optional[str] = Field(
        default=None, description="Slicer executable, overriding detection"
    )
    extra_args: Optional[list] = Field(
        default=None, description="Extra CLI arguments, appended verbatim"
    )
    timeout_s: Optional[float] = Field(
        default=None, description="Wall-clock budget (default FORGE_SLICE_TIMEOUT, 600)"
    )


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Nothing to do on startup; stop the worker process on shutdown.

    The worker is started lazily by the first request so importing this module
    (in tests, or for ``--help``) never spawns a process.
    """
    try:
        yield
    finally:
        shutdown_pool()


app = FastAPI(
    title="Forge geometry service",
    version=__version__,
    description="Build123d kernel for PartForge: PARAMS scripts in, printable solids out.",
    lifespan=_lifespan,
)


@app.exception_handler(ForgeError)
async def _forge_error_handler(_request: Request, exc: ForgeError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_payload())


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """A malformed request body is the caller's fault -> 400, not FastAPI's 422.

    The architecture contract only defines 400 and 500, so 422 never leaves
    this service.
    """
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', ()) if part != 'body')}: "
        f"{error.get('msg', 'invalid')}"
        for error in exc.errors()
    )
    return JSONResponse(
        status_code=400,
        content={"error": f"invalid request body: {problems}", "traceback": None},
    )


@app.get("/health")
def health() -> JSONResponse:
    """Liveness plus which build123d the worker actually has.

    Always answers 200 so a caller can tell "service down" (connection refused)
    from "service up but the kernel is missing" (status != "ok").
    """
    try:
        info = run_health(timeout=HEALTH_TIMEOUT_S)
    except ForgeError as exc:
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "build123d": None,
                "error": exc.message,
                "service": __version__,
                "slicer": health_detection(),
            },
        )

    version = info.get("build123d")
    payload: Dict[str, Any] = {
        "status": "ok" if version else "degraded",
        "build123d": version,
        "service": __version__,
        "python": info.get("python"),
        # Additive: whether /slice has anything to call.  A panel can grey out
        # its Slice button before the user presses it.  A missing slicer is not
        # a degraded service -- everything else still works.
        "slicer": health_detection(),
    }
    if not version:
        payload["error"] = info.get("build123d_error") or "build123d is not installed"
    return JSONResponse(status_code=200, content=payload)


@app.exception_handler(Exception)
async def _unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Anything not already classified is a service bug -> 500, same body shape."""
    return JSONResponse(
        status_code=500,
        content={
            "error": f"internal service error: {type(exc).__name__}: {exc}",
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        },
    )


@app.post("/parse_params")
def parse_params(request: ParseParamsRequest) -> JSONResponse:
    """Resolve a script's PARAMS schema.  No geometry is built."""
    result = run_parse_params(request.script, overrides=request.overrides)
    return JSONResponse(status_code=200, content={"params": result["params"]})


@app.post("/generate")
def generate(request: GenerateRequest) -> JSONResponse:
    """Build the part and return the mesh in millimetres.

    Returned as a raw ``JSONResponse`` on purpose: letting FastAPI run
    ``jsonable_encoder`` over a hundred thousand vertices would cost more than
    building the geometry did.
    """
    result = run_generate(
        request.script,
        request.overrides,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
    )
    payload = {
        "params": result["params"],
        "mesh": result["mesh"],
        "stats": result["stats"],
    }
    if "timings" in result:
        payload["timings"] = result["timings"]
    return JSONResponse(status_code=200, content=payload)


@app.post("/export")
def export(request: ExportRequest) -> JSONResponse:
    """Build the part and write it to an absolute path as STL, STEP or 3MF.

    Format and path are validated here, before the job is queued: a typo in the
    path should come back instantly, not after a build.
    """
    fmt = normalize_format(request.format)
    target = resolve_output_path(request.path, fmt)

    result = run_export(
        request.script,
        fmt,
        str(target),
        overrides=request.overrides,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
    )
    return JSONResponse(status_code=200, content={"path": result["path"]})


@app.post("/check")
def check(request: CheckRequest) -> JSONResponse:
    """Run the print-readiness checks: bed fit, wall thickness, overhangs, watertight.

    ``overall`` is the worst of the four statuses.  Nothing is written and no
    geometry is returned -- this is the "can I print this?" question on its own.
    """
    result = run_check(
        request.script,
        overrides=request.overrides,
        printer=request.printer,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
        min_wall_probe_mm=request.min_wall_probe_mm,
        max_wall_samples=request.max_wall_samples,
    )
    return JSONResponse(
        status_code=200,
        content={
            "overall": result["overall"],
            "checks": result["checks"],
            "printer": result["printer"],
            "params": result["params"],
            "stats": result["stats"],
            "timings": result["timings"],
        },
    )


@app.post("/segment")
def segment(request: SegmentRequest) -> JSONResponse:
    """Cut the part into printable segments with mating joints.

    Every segment is re-tessellated and re-checked before it comes back; a
    non-manifold segment is a 400, not a warning with a broken mesh attached.
    """
    result = run_segment(
        request.script,
        overrides=request.overrides,
        printer=request.printer,
        joint=request.joint,
        mode=request.mode,
        include_mesh=request.include_mesh,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
        plate_spacing_mm=request.plate_spacing_mm,
    )
    # Raw JSONResponse for the same reason /generate uses one: the segment
    # meshes are the bulk of the body and do not need re-encoding.
    return JSONResponse(status_code=200, content=result)


@app.post("/export_segments")
def export_segments(request: ExportSegmentsRequest) -> JSONResponse:
    """Write one file per segment plus a single 3MF plate laid out for the bed."""
    fmt = normalize_format(request.format)
    result = run_export_segments(
        request.script,
        request.directory,
        overrides=request.overrides,
        printer=request.printer,
        joint=request.joint,
        mode=request.mode,
        basename=request.basename,
        fmt=fmt,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
        plate_spacing_mm=request.plate_spacing_mm,
    )
    return JSONResponse(status_code=200, content=result)


@app.post("/check_mesh")
def check_mesh(request: CheckMeshRequest) -> JSONResponse:
    """The print-readiness checks against a mesh instead of a script.

    This is the front door for "fix this downloaded model": an STL off the
    internet gets the same four answers a generated part does.  ``params`` is
    ``null`` and ``stats.solid_is_valid`` is ``null`` -- there is no PARAMS
    schema and no B-Rep behind a mesh -- so ``watertight`` here is exactly the
    mesh half: closed, and consistently wound.
    """
    result = run_check_mesh(
        mesh=request.mesh,
        file_path=request.file_path,
        printer=request.printer,
        plate_margin_mm=request.plate_margin_mm,
        min_wall_probe_mm=request.min_wall_probe_mm,
        max_wall_samples=request.max_wall_samples,
        weld_tolerance_mm=request.weld_tolerance_mm,
    )
    return JSONResponse(
        status_code=200,
        content={
            "overall": result["overall"],
            "checks": result["checks"],
            "params": result["params"],
            "printer": result["printer"],
            "stats": result["stats"],
            "mesh_input": result["mesh_input"],
            "timings": result["timings"],
        },
    )


@app.post("/segment_mesh")
def segment_mesh(request: SegmentMeshRequest) -> JSONResponse:
    """Cut a downloaded model into printable segments with mating joints.

    The mesh is sewn into an OpenCascade solid and then goes through the very
    same cutting, joint and plate machinery ``/segment`` uses, so the joints,
    the watertight guarantee on every segment and the plate packing are not
    re-implementations -- they are the same code.

    Two refusals, both 400s and both before any kernel work: a mesh with holes
    in it cannot be cut (it says how to repair it), and a mesh with more
    triangles than the ceiling would take minutes rather than seconds (it says
    to decimate).
    """
    result = run_segment_mesh(
        mesh=request.mesh,
        file_path=request.file_path,
        printer=request.printer,
        joint=request.joint,
        mode=request.mode,
        include_mesh=request.include_mesh,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
        plate_spacing_mm=request.plate_spacing_mm,
        weld_tolerance_mm=request.weld_tolerance_mm,
        sew_tolerance_mm=request.sew_tolerance_mm,
        tri_limit=request.tri_limit,
    )
    return JSONResponse(status_code=200, content=result)


@app.post("/export_segments_mesh")
def export_segments_mesh(request: ExportSegmentsMeshRequest) -> JSONResponse:
    """``/segment_mesh``, written to disk: one file per segment plus the plate."""
    fmt = normalize_format(request.format)
    result = run_export_segments_mesh(
        request.directory,
        mesh=request.mesh,
        file_path=request.file_path,
        printer=request.printer,
        joint=request.joint,
        mode=request.mode,
        basename=request.basename,
        fmt=fmt,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
        plate_spacing_mm=request.plate_spacing_mm,
        weld_tolerance_mm=request.weld_tolerance_mm,
        sew_tolerance_mm=request.sew_tolerance_mm,
        tri_limit=request.tri_limit,
    )
    return JSONResponse(status_code=200, content=result)


@app.post("/mold")
def mold(request: MoldRequest) -> JSONResponse:
    """Produce a two-piece mold master from the part instead of the part.

    Both halves are re-tessellated and re-checked before they come back; a
    non-manifold half is a 400 naming it, not a warning with a broken mesh
    attached.
    """
    result = run_mold(
        request.script,
        overrides=request.overrides,
        printer=request.printer,
        options=request.mold_options(),
        include_mesh=request.include_mesh,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
    )
    # Raw JSONResponse for the same reason /generate uses one: the half meshes
    # are the bulk of the body and do not need re-encoding.
    return JSONResponse(status_code=200, content=result)


@app.post("/export_mold")
def export_mold(request: ExportMoldRequest) -> JSONResponse:
    """Write one file per mold half, each centred in XY and sitting on Z=0."""
    fmt = normalize_format(request.format)
    result = run_export_mold(
        request.script,
        request.directory,
        overrides=request.overrides,
        printer=request.printer,
        options=request.mold_options(),
        basename=request.basename,
        fmt=fmt,
        tolerance=request.tolerance,
        angular_tolerance=request.angular_tolerance,
        plate_margin_mm=request.plate_margin_mm,
    )
    return JSONResponse(status_code=200, content=result)


@app.post("/slice")
def slice_model(request: SliceRequest) -> JSONResponse:
    """Run the installed slicer's CLI over a file we exported.

    This is a subprocess call, not a geometry job, so it does not go near the
    warm worker.  With no slicer installed it is a 400 listing everything that
    was probed -- never a traceback about a missing file.
    """
    result = run_slice(
        request.input,
        request.output,
        profile=request.profile,
        filaments=request.filaments,
        slicer_path=request.slicer_path,
        printer=request.printer,
        extra_args=request.extra_args,
        timeout_s=request.timeout_s,
    )
    return JSONResponse(status_code=200, content=result)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-service",
        description="Forge geometry service (Build123d kernel for PartForge).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"loopback address to bind (default {DEFAULT_HOST}); "
        "non-loopback addresses are refused",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="wall-clock seconds a single script run may take "
        f"(default {DEFAULT_TIMEOUT_S:.0f})",
    )
    return parser


def run(argv: Optional[list] = None) -> int:
    """Console-script entry point (``forge-service``)."""
    import uvicorn  # noqa: PLC0415 - keep import cost out of `import service.main`

    args = build_arg_parser().parse_args(argv)

    if args.host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"refusing to bind {args.host!r}: the geometry service executes "
            "arbitrary scripts and must stay on loopback "
            f"({', '.join(sorted(LOOPBACK_HOSTS))})"
        )

    from .runner import get_pool  # noqa: PLC0415

    get_pool().timeout = float(args.timeout)

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
