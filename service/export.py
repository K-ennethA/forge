"""File export: STL (binary), STEP and 3MF, via build123d's exporters.

Runs in the worker process (it needs build123d), never in the HTTP process.

Paths come from the caller and must be absolute -- the service has no notion of
a project directory, and a relative path would resolve against whatever
directory the service happened to be started in.  Parent directories are
created.  Windows paths with spaces, backslashes and drive letters all work
because everything goes through :class:`pathlib.Path`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ParamError, ScriptError, ServiceError

#: format name -> conventional extension
FORMATS: Dict[str, str] = {
    "stl": ".stl",
    "step": ".step",
    "3mf": ".3mf",
}

#: Extensions we accept without complaint for a given format.
_ALIASES: Dict[str, tuple] = {
    "stl": (".stl",),
    "step": (".step", ".stp"),
    "3mf": (".3mf",),
}


def normalize_format(fmt: Any) -> str:
    """Validate and canonicalise a requested format name."""
    if not isinstance(fmt, str) or not fmt.strip():
        raise ParamError(
            f"format is required; one of {', '.join(sorted(FORMATS))}"
        )
    name = fmt.strip().lower().lstrip(".")
    if name == "stp":
        name = "step"
    if name not in FORMATS:
        raise ParamError(
            f"unsupported export format {fmt!r}; use one of "
            f"{', '.join(sorted(FORMATS))}"
        )
    return name


def resolve_output_path(path: Any, fmt: str) -> Path:
    """Turn the caller's path into an absolute :class:`Path`, making parents."""
    if not isinstance(path, str) or not path.strip():
        raise ParamError("path is required and must be an absolute file path")

    candidate = Path(path.strip())
    if not candidate.is_absolute():
        raise ParamError(
            f"path must be absolute, got {path!r}; the service does not guess a "
            "working directory"
        )

    # A directory (or a path ending in a separator) is not a file target.
    if candidate.is_dir():
        raise ParamError(f"path {str(candidate)!r} is an existing directory")

    if not candidate.suffix:
        candidate = candidate.with_suffix(FORMATS[fmt])

    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ParamError(
            f"cannot create the output directory {str(candidate.parent)!r}: {exc}"
        ) from exc

    return candidate


def export_shape(
    shape: Any,
    fmt: str,
    path: str,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
) -> str:
    """Write *shape* to *path* in *fmt* and return the absolute path written.

    ``tolerance`` (linear deflection, mm) and ``angular_tolerance`` (radians)
    only affect the tessellated formats, STL and 3MF.  STEP carries the exact
    B-Rep and ignores them.
    """
    from .runner import (  # noqa: PLC0415 - local import keeps the parent light
        DEFAULT_ANGULAR_TOLERANCE,
        DEFAULT_TOLERANCE_MM,
    )

    name = normalize_format(fmt)
    target = resolve_output_path(path, name)

    linear = float(tolerance) if tolerance else DEFAULT_TOLERANCE_MM
    angular = float(angular_tolerance) if angular_tolerance else DEFAULT_ANGULAR_TOLERANCE

    # If the caller's extension disagrees with `format` we honour the filename
    # they asked for -- projects have naming conventions -- and write the
    # content the format says.  `_ALIASES` documents the expected pairings.
    _ = _ALIASES[name]

    if name == "stl":
        _export_stl(shape, target, linear, angular)
    elif name == "step":
        _export_step(shape, target)
    else:
        _export_3mf(shape, target, linear, angular)

    if not target.exists():
        raise ServiceError(
            f"the {name.upper()} exporter reported success but wrote no file at "
            f"{str(target)!r}"
        )
    if target.stat().st_size == 0:
        raise ServiceError(f"the {name.upper()} exporter wrote an empty file")

    return str(target)


# --------------------------------------------------------------------------
# Per-format writers
# --------------------------------------------------------------------------


def _export_stl(shape: Any, target: Path, linear: float, angular: float) -> None:
    # VERIFY: build123d.export_stl(to_export, file_path, tolerance=...,
    #         angular_tolerance=..., ascii_format=False) -> bool
    from build123d import export_stl  # noqa: PLC0415

    try:
        ok = export_stl(
            shape,
            str(target),
            tolerance=linear,
            angular_tolerance=angular,
            ascii_format=False,  # binary STL: smaller and what slicers expect
        )
    except TypeError:
        # VERIFY: older/newer signatures may drop the keyword names.
        ok = export_stl(shape, str(target))
    except Exception as exc:  # noqa: BLE001
        raise _export_failure("STL", target, exc) from exc

    if ok is False:
        raise ServiceError(f"STL export failed for {str(target)!r}")


def _export_step(shape: Any, target: Path) -> None:
    # VERIFY: build123d.export_step(to_export, file_path, unit=Unit.MM, ...) -> bool
    from build123d import export_step  # noqa: PLC0415

    try:
        try:
            from build123d import Unit  # noqa: PLC0415

            ok = export_step(shape, str(target), unit=Unit.MM)
        except (ImportError, TypeError):
            ok = export_step(shape, str(target))
    except Exception as exc:  # noqa: BLE001
        raise _export_failure("STEP", target, exc) from exc

    if ok is False:
        raise ServiceError(f"STEP export failed for {str(target)!r}")


def _export_3mf(shape: Any, target: Path, linear: float, angular: float) -> None:
    # VERIFY: build123d.Mesher(unit=Unit.MM); Mesher.add_shape(shape,
    #         linear_deflection=..., angular_deflection=...); Mesher.write(path)
    from build123d import Mesher  # noqa: PLC0415

    try:
        try:
            from build123d import Unit  # noqa: PLC0415

            mesher = Mesher(unit=Unit.MM)
        except (ImportError, TypeError):
            mesher = Mesher()

        try:
            mesher.add_shape(
                shape, linear_deflection=linear, angular_deflection=angular
            )
        except TypeError:
            mesher.add_shape(shape)

        mesher.write(str(target))
    except Exception as exc:  # noqa: BLE001
        raise _export_failure("3MF", target, exc) from exc


def _export_failure(label: str, target: Path, exc: Exception) -> Exception:
    """Classify an exporter blow-up: the caller's path vs. our kernel."""
    if isinstance(exc, (PermissionError, FileNotFoundError, IsADirectoryError, OSError)):
        return ScriptError(
            f"{label} export could not write {str(target)!r}: {exc}"
        )
    return ServiceError(f"{label} export failed for {str(target)!r}: {exc}")


__all__ = [
    "FORMATS",
    "export_shape",
    "normalize_format",
    "resolve_output_path",
]
