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
from .errors import ForgeError, ScriptError, ServiceError
from .export import export_shape
from .runner import (
    DEFAULT_ANGULAR_TOLERANCE,
    DEFAULT_TOLERANCE_MM,
    compute_stats,
    normalize_build_result,
    tessellate_shape,
    weld_vertices,
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


HANDLERS = {
    "health": handle_health,
    "parse_params": handle_parse_params,
    "generate": handle_generate,
    "export": handle_export,
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
