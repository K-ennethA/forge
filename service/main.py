"""FastAPI application and runner for the Forge geometry service.

Implements the HTTP API from ``docs/architecture.md`` on 127.0.0.1:8765::

    GET  /health        -> {"status": "ok", "build123d": "<version>"}
    POST /parse_params  -> {"params": <resolved schema>}
    POST /generate      -> {"params": ..., "mesh": {...}, "stats": {...}}
    POST /export        -> {"path": "<absolute path written>"}

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
    run_export,
    run_generate,
    run_health,
    run_parse_params,
    shutdown_pool,
)

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
            },
        )

    version = info.get("build123d")
    payload: Dict[str, Any] = {
        "status": "ok" if version else "degraded",
        "build123d": version,
        "service": __version__,
        "python": info.get("python"),
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
