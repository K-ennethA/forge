"""Path handling and result formatting shared by the tools.

Windows is the primary platform (docs/architecture.md ground rules): paths
arrive with backslashes, forward slashes, spaces, `~`, or `%VAR%`, and the
geometry service wants an absolute path. Everything funnels through
:func:`resolve_path`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .errors import ForgeError

# Script filenames that say nothing about the part; the folder name does.
_GENERIC_STEMS = {"part", "main", "model", "script", "build", "generate", "__init__"}

# Blender caps object names at 63 bytes.
_MAX_OBJECT_NAME = 63


def resolve_path(raw: str, *, must_exist: bool = False, label: str = "path") -> Path:
    """Expand ``~``/env vars and return an absolute, normalized Path."""
    if raw is None or not str(raw).strip():
        raise ForgeError(f"No {label} given.")
    expanded = os.path.expandvars(os.path.expanduser(str(raw).strip().strip('"')))
    path = Path(expanded)
    try:
        path = path.resolve()
    except OSError:
        path = Path(os.path.abspath(expanded))
    if must_exist and not path.is_file():
        if path.is_dir():
            raise ForgeError(f"The {label} {path} is a directory, not a file.")
        raise ForgeError(f"No file at {path} (resolved from {raw!r}).")
    return path


def read_script(script_path: str) -> tuple[Path, str]:
    """Read a PartForge script from disk, returning (absolute path, source)."""
    path = resolve_path(script_path, must_exist=True, label="script path")
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{path} is not valid UTF-8 Python source: {exc}") from exc
    except OSError as exc:
        raise ForgeError(f"Could not read {path}: {exc}") from exc
    if not source.strip():
        raise ForgeError(f"{path} is empty.")
    return path, source


def object_name_for_script(path: Path) -> str:
    """Derive the Blender object name for a generated part.

    The script's filename, except that ``projects/bowl_holder/part.py`` is
    obviously "bowl_holder" and not "part".
    """
    stem = path.stem
    if stem.lower() in _GENERIC_STEMS and path.parent.name:
        stem = path.parent.name
    stem = stem.strip() or "Part"
    encoded = stem.encode("utf-8")[:_MAX_OBJECT_NAME]
    return encoded.decode("utf-8", errors="ignore") or "Part"


def ensure_parent_dir(path: Path) -> None:
    """Create the output directory so an export never fails on a missing folder."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ForgeError(f"Could not create the output directory {path.parent}: {exc}") from exc


# --- formatting -------------------------------------------------------------


def fmt_number(value: Any, places: int = 3) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.{places}f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def fmt_vector(values: Any, places: int = 3) -> str:
    if not isinstance(values, (list, tuple)):
        return str(values)
    return "(" + ", ".join(fmt_number(v, places) for v in values) + ")"


def fmt_scene_info(result: Mapping[str, Any]) -> str:
    """Render get_scene_info into a compact table."""
    objects: Iterable[Any] = result.get("objects") or []
    objects = list(objects)
    active = result.get("active")

    if not objects:
        return "Scene is empty (no objects)."

    lines = [f"{len(objects)} object(s); active: {active if active else 'none'}", ""]
    lines.append(
        f"{'name':<24} {'type':<10} {'verts':>8}  {'location':<26} "
        f"{'dimensions':<26} modifiers"
    )
    for obj in objects:
        if not isinstance(obj, Mapping):
            lines.append(f"  {obj!r}")
            continue
        name = str(obj.get("name", "?"))
        marker = "*" if active and name == active else " "
        modifiers = obj.get("modifiers") or []
        mods = ", ".join(str(m) for m in modifiers) if modifiers else "-"
        lines.append(
            f"{marker}{name:<23.23} {str(obj.get('type', '?')):<10.10} "
            f"{str(obj.get('vertex_count', '?')):>8} "
            f"{fmt_vector(obj.get('location')):<26.26} "
            f"{fmt_vector(obj.get('dimensions')):<26.26} {mods}"
        )
    lines.append("")
    lines.append("(* = active object)")
    return "\n".join(lines)


def fmt_params(params: Any) -> str:
    """Render a resolved PARAMS schema as one line per parameter."""
    if not isinstance(params, Mapping) or not params:
        return "(no parameters declared)"
    lines = []
    for name, spec in params.items():
        if not isinstance(spec, Mapping):
            lines.append(f"  {name} = {spec!r}")
            continue
        value = fmt_number(spec.get("value"))
        unit = spec.get("unit") or ""
        bits = [f"  {name} = {value}{(' ' + str(unit)) if unit else ''}"]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None or hi is not None:
            bits.append(f"[{fmt_number(lo) if lo is not None else '-'}"
                        f"..{fmt_number(hi) if hi is not None else '-'}]")
        step = spec.get("step")
        if step is not None:
            bits.append(f"step {fmt_number(step)}")
        description = spec.get("description")
        if description:
            bits.append(f"— {description}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def fmt_stats(stats: Any) -> str:
    if not isinstance(stats, Mapping):
        return str(stats)
    bbox = stats.get("bounding_box_mm")
    watertight = stats.get("watertight")
    parts = [
        f"vertices {stats.get('vertex_count', '?')}",
        f"faces {stats.get('face_count', '?')}",
    ]
    if bbox is not None:
        parts.append(f"bbox {fmt_vector(bbox, 2)} mm")
    if watertight is not None:
        parts.append("watertight" if watertight else "NOT WATERTIGHT")
    return ", ".join(parts)


def fmt_overrides(overrides: Optional[Dict[str, Any]]) -> str:
    if not overrides:
        return "none"
    return ", ".join(f"{k}={fmt_number(v)}" for k, v in overrides.items())


def ok(message: str, detail: str = "") -> str:
    return f"OK — {message}" + (f" ({detail})" if detail else "")
