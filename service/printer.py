"""Printer profiles: the numbers every print-readiness decision is measured against.

A profile is the JSON object from ``templates/printer.json`` -- bed size, nozzle,
minimum wall, overhang limit and the fit tolerances.  Callers pass it verbatim in
the request body of ``/check``, ``/segment`` and ``/export_segments``; anything
they leave out falls back to :data:`DEFAULT_PROFILE`.

The defaults here *mirror* ``templates/printer.json`` (Elegoo Centauri Carbon)
rather than reading it.  The service has no notion of the repository layout --
it is handed scripts and profiles, it does not go looking for files -- and a
copy that drifts is easier to notice than a path that silently resolves
somewhere else.  Keep the two in step when the template changes.

Nothing in this module imports build123d: it is plain arithmetic on a dict and
runs happily in the HTTP process as well as in the worker.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import ParamError

#: Mirror of ``templates/printer.json``.  Units are millimetres unless noted.
DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "Elegoo Centauri Carbon",
    "slicer": "orcaslicer",
    "bed": {"x": 256.0, "y": 256.0, "z": 256.0},
    "nozzle_diameter": 0.4,
    "layer_heights": {"default": 0.2, "min": 0.08, "max": 0.28},
    "min_wall_thickness": 0.8,
    "min_feature_size": 1.0,
    "max_unsupported_overhang_deg": 50.0,
    "tolerances": {
        "press_fit": 0.1,
        "slide_fit": 0.2,
        "loose_fit": 0.3,
        "magnet_pocket_extra": 0.05,
    },
    "materials": ["PLA", "PETG", "ABS", "ASA", "PLA-CF", "PETG-CF"],
}

#: Free space kept between the outermost part and the bed edge, per side.
DEFAULT_PLATE_MARGIN_MM = 5.0

#: Free space kept between two parts on the plate.
DEFAULT_PLATE_SPACING_MM = 5.0


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"printer.{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ParamError(f"printer.{label} must be a finite positive number, got {value!r}")
    return number


def normalize_printer(profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Merge *profile* over :data:`DEFAULT_PROFILE` and validate the numbers used.

    Only the keys this service actually reads are validated; unknown keys ride
    along untouched so a profile can carry slicer settings we do not parse yet.
    """
    merged: Dict[str, Any] = copy.deepcopy(DEFAULT_PROFILE)
    if profile is None:
        return merged
    if not isinstance(profile, Mapping):
        raise ParamError(f"printer must be an object, got {type(profile).__name__}")

    for key, value in profile.items():
        if key in ("bed", "tolerances", "layer_heights") and isinstance(value, Mapping):
            nested = dict(merged.get(key) or {})
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value

    bed = merged.get("bed")
    if not isinstance(bed, Mapping):
        raise ParamError("printer.bed must be an object with x, y and z sizes in mm")
    merged["bed"] = {
        "x": _positive(bed.get("x"), "bed.x"),
        "y": _positive(bed.get("y"), "bed.y"),
        "z": _positive(bed.get("z"), "bed.z"),
    }

    merged["min_wall_thickness"] = _positive(
        merged.get("min_wall_thickness"), "min_wall_thickness"
    )
    merged["min_feature_size"] = _positive(
        merged.get("min_feature_size"), "min_feature_size"
    )
    merged["nozzle_diameter"] = _positive(merged.get("nozzle_diameter"), "nozzle_diameter")

    overhang = merged.get("max_unsupported_overhang_deg")
    if isinstance(overhang, bool) or not isinstance(overhang, (int, float)):
        raise ParamError(
            f"printer.max_unsupported_overhang_deg must be a number, got {overhang!r}"
        )
    overhang = float(overhang)
    if not 0.0 <= overhang <= 90.0:
        raise ParamError(
            "printer.max_unsupported_overhang_deg is an angle from vertical and must "
            f"sit in [0, 90], got {overhang}"
        )
    merged["max_unsupported_overhang_deg"] = overhang

    tolerances = merged.get("tolerances")
    if not isinstance(tolerances, Mapping):
        raise ParamError("printer.tolerances must be an object")
    clean_tolerances: Dict[str, float] = {}
    for key, value in tolerances.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamError(f"printer.tolerances.{key} must be a number, got {value!r}")
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise ParamError(
                f"printer.tolerances.{key} must be a finite non-negative number, "
                f"got {value!r}"
            )
        clean_tolerances[str(key)] = number
    for required in ("press_fit", "magnet_pocket_extra"):
        clean_tolerances.setdefault(
            required, float(DEFAULT_PROFILE["tolerances"][required])
        )
    merged["tolerances"] = clean_tolerances

    return merged


def bed_size(printer: Mapping[str, Any]) -> Tuple[float, float, float]:
    bed = printer["bed"]
    return (float(bed["x"]), float(bed["y"]), float(bed["z"]))


def tolerance(printer: Mapping[str, Any], name: str, default: float = 0.1) -> float:
    """One named fit tolerance, in millimetres."""
    tolerances = printer.get("tolerances") or {}
    value = tolerances.get(name)
    if value is None:
        return float(default)
    return float(value)


__all__ = [
    "DEFAULT_PLATE_MARGIN_MM",
    "DEFAULT_PLATE_SPACING_MM",
    "DEFAULT_PROFILE",
    "bed_size",
    "normalize_printer",
    "tolerance",
]
