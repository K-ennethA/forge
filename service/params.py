"""The PARAMS block contract -- the PartForge script interface.

A PartForge script is a plain Python file with, at top level::

    PARAMS = {
        "outer_diameter": {"value": 20.0, "unit": "mm", "min": 5.0, "max": 100.0,
                           "step": 0.1, "description": "Outer diameter"},
    }

    def build(p):
        # p is {param_name: resolved value}, already validated, inches already
        # converted to millimetres.  Returns a Build123d Part/Solid/Compound.
        ...

Rules (from ``docs/architecture.md``):

* ``value`` is required.
* ``unit`` is one of ``mm``, ``in``, ``deg``, ``count``, ``ratio``, ``bool``.
* ``min`` / ``max`` / ``step`` / ``description`` are optional.
* ``in`` values are converted to mm before ``build()`` is called.
* The service validates overrides against ``min``/``max`` and type.

Two vocabularies, deliberately kept apart
-----------------------------------------
*Panel units*  -- what the Blender panel shows and what ``overrides`` are
expressed in: the unit the script declared.  A param declared ``"unit": "in"``
with ``"value": 0.5`` shows 0.5 in the panel, and an override of ``0.75`` means
0.75 inches.  The resolved schema returned by ``/parse_params`` and ``/generate``
is always in panel units.

*Build units* -- what ``build(p)`` receives: millimetres for ``mm``/``in``,
degrees for ``deg``, ``int`` for ``count``, ``float`` for ``ratio``, ``bool``
for ``bool``.  ``build()`` never sees inches.

``step`` is a UI hint only.  The service does not snap values to it; snapping
belongs in the panel where the user can see it happen.

Security note: :func:`exec_script` is *not* a sandbox.  PartForge scripts are
authored by Claude on the user's machine and run with full privileges, exactly
like any other local Python file.  The "controlled namespace" here is about
determinism and clean error reporting, not confinement.  The wall-clock timeout
in :mod:`runner` is the only containment, and it exists so a runaway loop cannot
wedge the service.
"""

from __future__ import annotations

import copy
import linecache
import math
import sys
import traceback
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .errors import ParamError, ScriptError

# --------------------------------------------------------------------------
# Contract constants
# --------------------------------------------------------------------------

ALLOWED_UNITS: Tuple[str, ...] = ("mm", "in", "deg", "count", "ratio", "bool")

#: Units whose values are plain floats in panel units.
FLOAT_UNITS: Tuple[str, ...] = ("mm", "in", "deg", "ratio")

MM_PER_INCH = 25.4

#: Keys the panel understands.  Unknown keys are preserved verbatim so scripts
#: can carry forward-compatible hints (groups, labels, enum choices) without the
#: service having to know about them.
KNOWN_SPEC_KEYS = ("value", "unit", "min", "max", "step", "description")

#: The default filename scripts are compiled under.  It shows up in tracebacks.
SCRIPT_FILENAME = "<partforge script>"

#: How far a float may sit from an integer and still be accepted for a "count".
_COUNT_EPS = 1e-6


# --------------------------------------------------------------------------
# Script execution
# --------------------------------------------------------------------------


def install_forge_lib(namespace: Dict[str, Any]) -> None:
    """Make ``forge_lib`` available to a script the same way build123d is.

    Two routes, because scripts are written by hand as often as by Claude:
    ``import forge_lib`` works (the module is registered under its bare name in
    ``sys.modules``), and the name is already bound in the script's namespace so
    a script that forgets the import still runs.  The package is importable as
    both ``service`` and ``forge_service``, so the relative import here is what
    keeps the bare name pointing at the right module either way.

    A failure is swallowed: the appendage library is a convenience, and a script
    that does not use it must not stop working because it could not be loaded.
    """
    try:
        from . import forge_lib  # noqa: PLC0415 - avoids an import cycle at module load
    except Exception:  # noqa: BLE001 - never fail a script over an extra
        return
    sys.modules.setdefault("forge_lib", forge_lib)
    namespace.setdefault("forge_lib", forge_lib)


def install_maker_lib(namespace: Dict[str, Any]) -> None:
    """Make ``maker_lib`` available to a script exactly the way ``forge_lib`` is.

    Maker mode's library is a peer of the printability library, not an extra on
    top of it: a script that designs around a real switch imports it the same
    way, ``import maker_lib``, and finds the name already bound if it forgets.
    ``components`` and ``wiring`` ride along under their own bare names too, so
    a script can reach the raw data table or the circuit maths without going
    through the geometry module.

    A failure is swallowed for the same reason ``install_forge_lib``'s is: a
    script that does not use maker mode must not stop working because maker mode
    could not be loaded.
    """
    try:
        from . import components, maker_lib, wiring  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - never fail a script over an extra
        return
    for name, module in (
        ("maker_lib", maker_lib),
        ("components", components),
        ("wiring", wiring),
    ):
        sys.modules.setdefault(name, module)
        namespace.setdefault(name, module)


def exec_script(source: str, filename: str = SCRIPT_FILENAME) -> Dict[str, Any]:
    """Execute *source* in a fresh namespace and return that namespace.

    Raises :class:`ScriptError` (HTTP 400) for syntax errors and for anything the
    script raises while its top level runs.  The source is registered with
    :mod:`linecache` first so the traceback we hand back shows the offending
    lines instead of a bare frame.
    """
    if not isinstance(source, str):
        raise ParamError("script must be a string of Python source")
    if not source.strip():
        raise ParamError("script is empty")

    # Make the script's source visible to traceback formatting.
    linecache.cache[filename] = (
        len(source),
        None,
        source.splitlines(keepends=True),
        filename,
    )

    namespace: Dict[str, Any] = {
        "__name__": "partforge_script",
        "__file__": filename,
        "__doc__": None,
        # __builtins__ is injected by exec(); scripts need the full builtins to
        # import build123d and do arithmetic.
    }
    install_forge_lib(namespace)
    install_maker_lib(namespace)

    try:
        code = compile(source, filename, "exec")
    except SyntaxError as exc:
        raise ScriptError(
            f"syntax error in script at line {exc.lineno}: {exc.msg}",
            traceback.format_exc(),
        ) from exc

    try:
        exec(code, namespace)  # noqa: S102 - running the user's script is the job
    except ScriptError:
        raise
    except BaseException as exc:  # noqa: BLE001 - report anything the script does
        raise ScriptError(
            f"script raised while loading: {type(exc).__name__}: {exc}",
            traceback.format_exc(),
        ) from exc

    return namespace


def extract_params(namespace: Mapping[str, Any]) -> Mapping[str, Any]:
    """Pull the raw ``PARAMS`` mapping out of an executed namespace."""
    if "PARAMS" not in namespace:
        raise ParamError(
            "script defines no PARAMS block; every PartForge script needs a "
            "top-level PARAMS dict"
        )
    raw = namespace["PARAMS"]
    if not isinstance(raw, Mapping):
        raise ParamError(
            f"PARAMS must be a dict, got {type(raw).__name__}"
        )
    return raw


def extract_build(namespace: Mapping[str, Any]) -> Callable[[Dict[str, Any]], Any]:
    """Pull the ``build`` callable out of an executed namespace."""
    if "build" not in namespace:
        raise ParamError(
            "script defines no build(p) function; every PartForge script needs a "
            "top-level def build(p)"
        )
    build = namespace["build"]
    if not callable(build):
        raise ParamError(f"build must be callable, got {type(build).__name__}")
    return build


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(name: str, key: str, value: float) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ParamError(f"parameter '{name}': {key} must be a finite number, got {value!r}")
    return number


def validate_schema(raw: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Validate a raw ``PARAMS`` mapping and return a normalised deep copy.

    The copy keeps every key the script declared (including unknown ones) but
    guarantees that ``value`` and ``unit`` are present and legal, that
    ``min``/``max``/``step`` are finite numbers when present, that ``min <= max``,
    that ``description`` is a string when present, and that the declared default
    sits inside ``[min, max]``.
    """
    if not isinstance(raw, Mapping):
        raise ParamError(f"PARAMS must be a dict, got {type(raw).__name__}")

    schema: Dict[str, Dict[str, Any]] = {}

    for name, spec in raw.items():
        if not isinstance(name, str):
            raise ParamError(f"PARAMS keys must be strings, got {name!r}")
        if not name.isidentifier():
            raise ParamError(
                f"parameter name '{name}' is not a valid Python identifier; the "
                "Blender panel needs identifiers for its properties"
            )
        if not isinstance(spec, Mapping):
            raise ParamError(
                f"parameter '{name}': spec must be a dict, got {type(spec).__name__}"
            )

        entry: Dict[str, Any] = copy.deepcopy(dict(spec))

        if "value" not in entry:
            raise ParamError(f"parameter '{name}': 'value' is required")

        unit = entry.get("unit")
        if unit is None:
            raise ParamError(
                f"parameter '{name}': 'unit' is required; one of "
                f"{', '.join(ALLOWED_UNITS)}"
            )
        if not isinstance(unit, str) or unit not in ALLOWED_UNITS:
            raise ParamError(
                f"parameter '{name}': unit {unit!r} is not supported; use one of "
                f"{', '.join(ALLOWED_UNITS)}"
            )

        for bound in ("min", "max"):
            if entry.get(bound) is not None:
                if not _is_number(entry[bound]):
                    raise ParamError(
                        f"parameter '{name}': {bound} must be a number, got "
                        f"{entry[bound]!r}"
                    )
                entry[bound] = _finite(name, bound, entry[bound])

        if entry.get("min") is not None and entry.get("max") is not None:
            if entry["min"] > entry["max"]:
                raise ParamError(
                    f"parameter '{name}': min ({entry['min']}) is greater than max "
                    f"({entry['max']})"
                )

        if entry.get("step") is not None:
            if not _is_number(entry["step"]):
                raise ParamError(
                    f"parameter '{name}': step must be a number, got {entry['step']!r}"
                )
            entry["step"] = _finite(name, "step", entry["step"])
            if entry["step"] <= 0:
                raise ParamError(
                    f"parameter '{name}': step must be greater than zero, got "
                    f"{entry['step']}"
                )

        if entry.get("description") is not None and not isinstance(
            entry["description"], str
        ):
            raise ParamError(
                f"parameter '{name}': description must be a string, got "
                f"{type(entry['description']).__name__}"
            )

        # The declared default must itself be legal -- a script whose default is
        # out of its own range is a bug we want to hear about immediately.
        entry["value"] = coerce_value(name, entry, entry["value"], source="PARAMS default")
        check_range(name, entry, entry["value"], source="PARAMS default")

        schema[name] = entry

    return schema


# --------------------------------------------------------------------------
# Coercion and range checking
# --------------------------------------------------------------------------


def coerce_value(
    name: str,
    spec: Mapping[str, Any],
    value: Any,
    source: str = "override",
) -> Any:
    """Coerce *value* to the type implied by ``spec['unit']`` (panel units)."""
    unit = spec.get("unit", "mm")

    if unit == "bool":
        return _coerce_bool(name, value, source)
    if unit == "count":
        return _coerce_count(name, value, source)
    return _coerce_float(name, value, source, unit)


def _coerce_bool(name: str, value: Any, source: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ParamError(
        f"parameter '{name}' ({source}): expected a boolean, got {value!r}"
    )


def _coerce_count(name: str, value: Any, source: str) -> int:
    if isinstance(value, bool):
        raise ParamError(
            f"parameter '{name}' ({source}): expected an integer count, got a bool"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ParamError(
                f"parameter '{name}' ({source}): expected a finite count, got {value!r}"
            )
        nearest = round(value)
        if abs(value - nearest) > _COUNT_EPS:
            raise ParamError(
                f"parameter '{name}' ({source}): expected a whole number for a "
                f"'count' parameter, got {value!r}"
            )
        return int(nearest)
    if isinstance(value, str):
        try:
            return _coerce_count(name, float(value.strip()), source)
        except (ValueError, ParamError):
            raise ParamError(
                f"parameter '{name}' ({source}): expected an integer count, got "
                f"{value!r}"
            ) from None
    raise ParamError(
        f"parameter '{name}' ({source}): expected an integer count, got {value!r}"
    )


def _coerce_float(name: str, value: Any, source: str, unit: str) -> float:
    if isinstance(value, bool):
        raise ParamError(
            f"parameter '{name}' ({source}): expected a number ({unit}), got a bool"
        )
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ParamError(
                f"parameter '{name}' ({source}): expected a number ({unit}), got "
                f"{value!r}"
            ) from None
    else:
        raise ParamError(
            f"parameter '{name}' ({source}): expected a number ({unit}), got "
            f"{type(value).__name__}"
        )

    if math.isnan(number) or math.isinf(number):
        raise ParamError(
            f"parameter '{name}' ({source}): expected a finite number, got {value!r}"
        )
    return number


def check_range(
    name: str,
    spec: Mapping[str, Any],
    value: Any,
    source: str = "override",
    clamp: bool = False,
) -> Any:
    """Enforce ``min``/``max`` in panel units.

    Ranges do not apply to ``bool`` parameters.  With ``clamp=False`` (the
    default and the documented behaviour) an out-of-range value is rejected with
    an error naming the parameter, its value and the bound it broke.  With
    ``clamp=True`` the value is pulled to the nearest bound instead -- the panel
    uses that mode when it wants a slider to saturate rather than error.
    """
    if spec.get("unit") == "bool":
        return value

    minimum = spec.get("min")
    maximum = spec.get("max")
    unit = spec.get("unit", "mm")

    is_count = spec.get("unit") == "count"

    if minimum is not None and value < minimum:
        if clamp:
            # Counts clamp *inward*: ceil the minimum so the result still
            # satisfies the bound.
            return int(math.ceil(minimum)) if is_count else float(minimum)
        raise ParamError(
            f"parameter '{name}' ({source}): {value} {unit} is below its minimum "
            f"of {minimum} {unit}"
        )
    if maximum is not None and value > maximum:
        if clamp:
            return int(math.floor(maximum)) if is_count else float(maximum)
        raise ParamError(
            f"parameter '{name}' ({source}): {value} {unit} is above its maximum "
            f"of {maximum} {unit}"
        )
    return value


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def to_build_value(name: str, spec: Mapping[str, Any], value: Any) -> Any:
    """Convert one panel-unit value into the value ``build(p)`` receives."""
    unit = spec.get("unit", "mm")
    if unit == "in":
        return float(value) * MM_PER_INCH
    if unit == "bool":
        return bool(value)
    if unit == "count":
        return int(value)
    # mm stays mm, deg stays degrees (build123d angles are degrees), ratio is a
    # plain float.
    return float(value)


def resolve(
    raw_schema: Mapping[str, Any],
    overrides: Optional[Mapping[str, Any]] = None,
    clamp: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Validate the schema, apply overrides, and produce both views.

    Returns ``(schema, build_values)`` where

    * ``schema`` is the fully resolved PARAMS schema in *panel units*, with
      ``value`` set to the current value -- this is what ``/parse_params`` and
      ``/generate`` hand back and what the Blender panel is generated from.
    * ``build_values`` is ``{name: value}`` in *build units* -- inches already
      multiplied by 25.4 -- and is what gets passed to ``build(p)``.

    Unknown override names are rejected (they are almost always typos), as are
    values that fail type coercion or fall outside ``min``/``max``.
    """
    schema = validate_schema(raw_schema)

    if overrides:
        if not isinstance(overrides, Mapping):
            raise ParamError(
                f"overrides must be a dict, got {type(overrides).__name__}"
            )
        for name, value in overrides.items():
            if name not in schema:
                known = ", ".join(sorted(schema)) or "(none)"
                raise ParamError(
                    f"unknown parameter '{name}' in overrides; this script "
                    f"declares: {known}"
                )
            spec = schema[name]
            coerced = coerce_value(name, spec, value, source="override")
            coerced = check_range(name, spec, coerced, source="override", clamp=clamp)
            spec["value"] = coerced

    build_values = {
        name: to_build_value(name, spec, spec["value"]) for name, spec in schema.items()
    }
    return schema, build_values


def load_script(
    source: str,
    overrides: Optional[Mapping[str, Any]] = None,
    clamp: bool = False,
    filename: str = SCRIPT_FILENAME,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any], Callable[[Dict[str, Any]], Any]]:
    """Execute a script and resolve its parameters in one step.

    Returns ``(schema, build_values, build_fn)``.
    """
    namespace = exec_script(source, filename=filename)
    raw = extract_params(namespace)
    build_fn = extract_build(namespace)
    schema, build_values = resolve(raw, overrides, clamp=clamp)
    return schema, build_values, build_fn


__all__ = [
    "ALLOWED_UNITS",
    "FLOAT_UNITS",
    "MM_PER_INCH",
    "SCRIPT_FILENAME",
    "check_range",
    "coerce_value",
    "exec_script",
    "extract_build",
    "extract_params",
    "install_forge_lib",
    "install_maker_lib",
    "load_script",
    "resolve",
    "to_build_value",
    "validate_schema",
]
