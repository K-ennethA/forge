"""Path handling and result formatting shared by the tools.

Windows is the primary platform (docs/architecture.md ground rules): paths
arrive with backslashes, forward slashes, spaces, `~`, or `%VAR%`, and the
geometry service wants an absolute path. Everything funnels through
:func:`resolve_path`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import config
from .errors import ForgeError

# Script filenames that say nothing about the part; the folder name does.
_GENERIC_STEMS = {"part", "main", "model", "script", "build", "generate", "__init__"}

# Blender caps object names at 63 bytes.
_MAX_OBJECT_NAME = 63


def resolve_path(raw: str, *, must_exist: bool = False, label: str = "path") -> Path:
    """Expand ``~``/env vars and return an absolute, normalized Path."""
    # Quotes come off before the empty check, so a bare '""' is "no path given"
    # rather than silently resolving to the server's working directory.
    cleaned = "" if raw is None else str(raw).strip().strip('"').strip()
    if not cleaned:
        raise ForgeError(f"No {label} given.")
    expanded = os.path.expandvars(os.path.expanduser(cleaned))
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


def read_printer(printer_path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Load a printer profile for the Phase 2 endpoints.

    Returns ``(profile_or_None, source_label)``. A missing *default* profile is
    not an error — the service merges over its own built-in Centauri Carbon
    profile, so ``None`` simply means "use the service's defaults". A missing
    profile the caller asked for by name IS an error, because silently printing
    against the wrong bed is worse than failing.
    """
    raw = "" if printer_path is None else str(printer_path).strip()
    explicit = bool(raw.strip('"').strip())
    path = resolve_path(raw or config.DEFAULT_PRINTER_PATH, label="printer path")

    if not path.is_file():
        if explicit:
            raise ForgeError(
                f"No printer profile at {path}. Point printer_path at a "
                "printer.json (see templates/printer.json), or omit it to use "
                "the service's built-in Elegoo Centauri Carbon defaults."
            )
        return None, f"service defaults (no profile at {path})"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ForgeError(f"Could not read the printer profile {path}: {exc}") from exc
    try:
        profile = json.loads(text)
    except ValueError as exc:
        raise ForgeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(profile, dict):
        raise ForgeError(f"{path} must contain a JSON object, got {type(profile).__name__}.")
    return profile, str(path)


def normalize_segment_mode(mode: Any) -> Any:
    """Turn a caller-friendly `mode` into the wire form /segment expects.

    Accepted, in the order a model is likely to reach for one:

    * ``"auto"`` / omitted -> ``"auto"`` (the service picks from bed_fit)
    * ``4`` or ``"4"``     -> ``{"radial": 4}``
    * ``[30, 60]``         -> ``{"planar": [30.0, 60.0]}``
    * ``"30, 60"``         -> ``{"planar": [30.0, 60.0]}``
    * ``{"radial": 4}`` / ``{"planar": [...]}`` -> passed straight through, which
      is what makes partforge_check's `suggested_segmentation.mode` copy-pasteable.
    """
    if mode is None:
        return "auto"

    if isinstance(mode, Mapping):
        if "radial" in mode:
            count = _as_positive_int(mode.get("radial"), "mode radial count")
            out: Dict[str, Any] = {"radial": count}
            start = mode.get("start_angle_deg")
            if start is not None:
                out["start_angle_deg"] = float(start)
            return out
        if "planar" in mode:
            return {"planar": _as_z_list(mode.get("planar"))}
        raise ForgeError(
            "A mode object must carry 'radial' or 'planar', e.g. {\"radial\": 4} "
            f"or {{\"planar\": [30, 60]}}; got {dict(mode)!r}."
        )

    if isinstance(mode, bool):  # bool is an int; never a segment count
        raise ForgeError("mode must be \"auto\", a radial count, or a list of Z heights.")

    if isinstance(mode, int):
        return {"radial": _as_positive_int(mode, "mode radial count")}

    if isinstance(mode, float):
        if mode.is_integer():
            return {"radial": _as_positive_int(int(mode), "mode radial count")}
        raise ForgeError(f"A radial segment count must be a whole number; got {mode}.")

    if isinstance(mode, (list, tuple)):
        return {"planar": _as_z_list(mode)}

    text = str(mode).strip()
    if not text or text.lower() == "auto":
        return "auto"
    if text.startswith("{"):  # a JSON object handed over as a string
        try:
            return normalize_segment_mode(json.loads(text))
        except ValueError as exc:
            raise ForgeError(f"mode looked like JSON but did not parse: {exc}") from exc
    if text.startswith("["):
        try:
            return {"planar": _as_z_list(json.loads(text))}
        except ValueError as exc:
            raise ForgeError(f"mode looked like a list but did not parse: {exc}") from exc
    if "," in text:
        return {"planar": _as_z_list(text.split(","))}
    try:
        return {"radial": _as_positive_int(int(text), "mode radial count")}
    except (TypeError, ValueError):
        pass
    raise ForgeError(
        f"Could not read mode {mode!r}. Use \"auto\", a radial count like 4, or a "
        "list of Z heights like [30, 60]."
    )


def _as_positive_int(value: Any, label: str) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ForgeError(f"{label} must be a whole number; got {value!r}.") from exc
    if count < 2:
        raise ForgeError(f"{label} must be at least 2; got {count}.")
    return count


def _as_z_list(values: Any) -> List[float]:
    if not isinstance(values, (list, tuple)):
        raise ForgeError(f"Planar cut heights must be a list of Z values; got {values!r}.")
    heights: List[float] = []
    for value in values:
        try:
            heights.append(float(str(value).strip() if isinstance(value, str) else value))
        except (TypeError, ValueError) as exc:
            raise ForgeError(f"{value!r} is not a Z height in mm.") from exc
    if not heights:
        raise ForgeError("Planar mode needs at least one Z height to cut at.")
    return heights


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


# --- Phase 2 reports --------------------------------------------------------

_STATUS_TAG = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}


def _tag(status: Any) -> str:
    return _STATUS_TAG.get(str(status).lower(), f"[{str(status).upper()}]")


def _dims(values: Any, places: int = 2) -> str:
    """`[300.0, 300.0, 40.0]` -> `300 x 300 x 40 mm`."""
    if not isinstance(values, (list, tuple)) or not values:
        return "?"
    return " x ".join(fmt_number(v, places) for v in values) + " mm"


def _bed(printer: Any) -> str:
    bed = (printer or {}).get("bed") if isinstance(printer, Mapping) else None
    if not isinstance(bed, Mapping):
        return "?"
    return _dims([bed.get("x"), bed.get("y"), bed.get("z")], 0)


def _printer_line(printer: Any, source: str) -> str:
    name = printer.get("name") if isinstance(printer, Mapping) else None
    return f"printer: {name or 'unnamed'}, bed {_bed(printer)} — {source}"


def _bed_fit_line(data: Mapping[str, Any]) -> str:
    orientations = data.get("orientations") or []
    fitting = [o for o in orientations if isinstance(o, Mapping) and o.get("fits")]
    with_margin = [o for o in fitting if o.get("fits_with_margin")]
    names = ", ".join(str(o.get("orientation")) for o in with_margin or fitting) or "none"
    return (
        f"bbox {_dims(data.get('bounding_box_mm'))} vs bed {_dims(data.get('bed_mm'), 0)}; "
        f"{len(fitting)}/{len(orientations)} orientations fit "
        f"({len(with_margin)} with the {fmt_number(data.get('margin_mm'))} mm margin): {names}"
    )


def _suggestion_lines(data: Mapping[str, Any], indent: str) -> List[str]:
    """The bed_fit failure's segmentation advice, mode object included verbatim."""
    suggestion = data.get("suggested_segmentation")
    if not isinstance(suggestion, Mapping):
        return []
    lines = [f"{indent}suggested segmentation ({suggestion.get('kind', '?')}): "
             f"{suggestion.get('reason', '')}".rstrip()]
    if suggestion.get("feasible") and suggestion.get("mode") is not None:
        mode = json.dumps(suggestion.get("mode"), separators=(", ", ": "))
        lines.append(f"{indent}pass to partforge_segment verbatim -> mode = {mode}")
    else:
        lines.append(f"{indent}NOT segmentable automatically — cutting cannot fix this.")
    estimate = suggestion.get("estimated_segment_bbox_mm")
    if estimate:
        lines.append(f"{indent}estimated segment bbox {_dims(estimate)}")
    return lines


def _min_wall_line(data: Mapping[str, Any]) -> str:
    thinnest = data.get("min_measured_thickness_mm")
    measured = data.get("measured")
    capped = " (the probe limit; nothing thinner found)" if data.get("min_measured_is_capped") else ""
    if thinnest is None:
        head = (
            f"nothing thinner than the {fmt_number(data.get('probe_mm'))} mm probe "
            f"({data.get('unmeasured', '?')} of {data.get('sampled_facets', '?')} "
            "probes ran their full length)"
        )
    else:
        head = f"thinnest {fmt_number(thinnest)} mm of {measured} probes{capped}"
    head += (
        f"; min wall {fmt_number(data.get('min_wall_thickness_mm'))} mm, "
        f"min feature {fmt_number(data.get('min_feature_size_mm'))} mm"
    )
    regions = data.get("thin_regions") or []
    if regions and isinstance(regions[0], Mapping):
        head += f"; thinnest at {fmt_vector(regions[0].get('location_mm'), 2)} mm"
        below = data.get("below_min_wall") or 0
        if below:
            head += f", {below} probe(s) under the minimum wall"
    return head


def _overhang_line(data: Mapping[str, Any]) -> str:
    orientations = data.get("orientations") or {}
    best = data.get("best_orientation")
    current = data.get("current_orientation")

    def score(key: Any) -> str:
        entry = orientations.get(key) if isinstance(orientations, Mapping) else None
        if not isinstance(entry, Mapping):
            return "?"
        fraction = entry.get("unsupported_fraction")
        pct = f" ({fmt_number(float(fraction) * 100.0, 1)}% of area)" if fraction is not None else ""
        return f"{fmt_number(entry.get('unsupported_area_mm2'), 1)} mm2 unsupported{pct}"

    line = f"best orientation {best}: {score(best)}"
    if current and current != best:
        line += f"; as modelled ({current}): {score(current)}"
    limit = data.get("max_unsupported_overhang_deg")
    if limit is not None:
        line += f"; limit {fmt_number(limit)} deg from vertical"
    return line


def _watertight_line(data: Mapping[str, Any]) -> str:
    verdict = "watertight" if data.get("watertight") else "NOT watertight"
    return (
        f"{verdict}: B-Rep valid {fmt_number(data.get('solid_is_valid'))}, "
        f"mesh closed {fmt_number(data.get('mesh_is_closed'))}, "
        f"{data.get('boundary_edges', '?')} boundary / "
        f"{data.get('nonmanifold_edges', '?')} non-manifold edges"
    )


_CHECK_LINES = {
    "bed_fit": _bed_fit_line,
    "min_wall": _min_wall_line,
    "overhangs": _overhang_line,
    "watertight": _watertight_line,
}


def fmt_check_report(
    script_name: str,
    payload: Mapping[str, Any],
    overrides: Optional[Dict[str, Any]],
    printer_source: str,
) -> str:
    """Verdict first, then one line per check with the numbers that decide it."""
    overall = str(payload.get("overall", "?")).upper()
    lines = [
        f"Print readiness: {overall} — {script_name}",
        f"  {_printer_line(payload.get('printer'), printer_source)}",
        f"  overrides: {fmt_overrides(overrides)}",
        "",
    ]

    checks = payload.get("checks") or []
    if not checks:
        lines.append("  (the service returned no checks)")
        return "\n".join(lines)

    for check in checks:
        if not isinstance(check, Mapping):
            lines.append(f"  {check!r}")
            continue
        name = str(check.get("name", "?"))
        data = check.get("data") if isinstance(check.get("data"), Mapping) else {}
        builder = _CHECK_LINES.get(name)
        try:
            summary = builder(data) if builder else str(check.get("details", ""))
        except Exception:  # noqa: BLE001 - a report must never hide the verdict
            summary = str(check.get("details", ""))
        lines.append(f"  {_tag(check.get('status'))} {name:<11} {summary}")

        indent = " " * 21
        status = str(check.get("status", "")).lower()
        if name == "bed_fit":
            # The service's own `details` restates the summary line and then the
            # suggestion; only the suggestion is new, so print just that.
            if status == "fail":
                lines.extend(_suggestion_lines(data, indent))
        elif status != "pass" and check.get("details"):
            lines.append(f"{indent}{check['details']}")

    return "\n".join(lines)


def fmt_mode(mode: Any) -> str:
    if not isinstance(mode, Mapping):
        return str(mode)
    kind = mode.get("kind", "?")
    if kind == "radial":
        return (
            f"radial x{mode.get('count', '?')} "
            f"(start {fmt_number(mode.get('start_angle_deg', 0.0))} deg)"
        )
    if kind == "planar":
        heights = mode.get("heights") or mode.get("heights_mm") or mode.get("planar")
        return f"planar at Z {fmt_vector(heights, 2)} mm" if heights else "planar"
    if kind == "none":
        return "no cuts needed"
    return json.dumps(dict(mode), separators=(", ", ": "))


def fmt_joint(joint: Any) -> str:
    if not isinstance(joint, Mapping):
        return str(joint)
    text = str(joint.get("type", "?"))
    tolerance = joint.get("tolerance")
    if tolerance is not None:
        text += f" (tolerance {fmt_number(tolerance)} mm"
        source = joint.get("tolerance_source")
        text += f" from {source})" if source else ")"
    return text


def fmt_segment_report(script_name: str, payload: Mapping[str, Any],
                       overrides: Optional[Dict[str, Any]]) -> str:
    """Segments table + plate layout; the meshes themselves never appear here."""
    segments = [s for s in (payload.get("segments") or []) if isinstance(s, Mapping)]
    cuts = payload.get("cuts") or []
    pieces = sum(1 for s in segments if s.get("kind") != "hardware")
    hardware = len(segments) - pieces

    head = (
        f"Segmented {script_name} into {pieces} piece(s)"
        + (f" plus {hardware} printed pin(s)" if hardware else "")
    )
    lines = [
        head,
        f"  mode: {fmt_mode(payload.get('mode'))}   joint: {fmt_joint(payload.get('joint'))}"
        f"   cuts: {len(cuts)}",
        f"  overrides: {fmt_overrides(overrides)}",
        "",
        f"  {'segment':<20} {'kind':<9} {'oriented bbox (mm)':<26} {'verts':>7} "
        f"{'faces':>7}  watertight",
    ]
    for segment in segments:
        stats = segment.get("stats") if isinstance(segment.get("stats"), Mapping) else {}
        lines.append(
            f"  {str(segment.get('name', '?')):<20.20} "
            f"{str(segment.get('kind', '?')):<9.9} "
            f"{_dims(segment.get('oriented_bbox_mm')):<26.26} "
            f"{str(stats.get('vertex_count', '?')):>7} "
            f"{str(stats.get('face_count', '?')):>7}  "
            f"{'yes' if stats.get('watertight') else 'NO'}"
        )

    lines.append("")
    lines.append(fmt_plate(payload.get("plate")))
    return "\n".join(lines)


def fmt_plate(plate: Any) -> str:
    if not isinstance(plate, Mapping):
        return "  Plate: (not reported)"
    verdict = "fits" if plate.get("fits") else "DOES NOT FIT"
    lines = [
        f"  Plate: {verdict} the {_dims(plate.get('bed_mm'), 0)} bed — "
        f"{plate.get('rows', '?')} row(s), used {_dims(plate.get('used_mm'))}, "
        f"margin {fmt_number(plate.get('margin_mm'))} / spacing "
        f"{fmt_number(plate.get('spacing_mm'))} mm"
    ]
    for item in plate.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"    {str(item.get('name', '?')):<20.20} at "
            f"{fmt_vector(item.get('position_mm'), 1)} mm, rotated "
            f"{fmt_number(plate_rotation_deg(item), 2)} deg"
        )
    return "\n".join(lines)


def plate_rotation_deg(item: Mapping[str, Any]) -> float:
    """Total spin about Z for one plate item: pre_rotate_deg + rotate_deg."""
    total = 0.0
    for key in ("pre_rotate_deg", "rotate_deg"):
        try:
            total += float(item.get(key) or 0.0)
        except (TypeError, ValueError):
            pass
    return total


def plate_items_by_name(plate: Any) -> Dict[str, Dict[str, Any]]:
    """`plate.items` keyed by segment name, for matching meshes to positions."""
    items: Dict[str, Dict[str, Any]] = {}
    if not isinstance(plate, Mapping):
        return items
    for item in plate.get("items") or []:
        if isinstance(item, Mapping) and item.get("name"):
            items[str(item["name"])] = dict(item)
    return items


def fmt_size(num_bytes: Any) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "? bytes"
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def file_size(path: Any) -> Optional[int]:
    try:
        return Path(str(path)).stat().st_size
    except OSError:
        return None


def fmt_written_files(files: Sequence[Any], plate_path: Any) -> str:
    lines: List[str] = []
    total = 0
    for entry in files:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        size = file_size(path)
        if size is not None:
            total += size
        lines.append(
            f"  {str(entry.get('name', '?')):<20.20} {str(entry.get('kind', '?')):<9.9} "
            f"{fmt_size(size):>10}  {path}"
        )
    if plate_path:
        size = file_size(plate_path)
        if size is not None:
            total += size
        lines.append(f"  {'(packed plate)':<20} {'plate':<9} {fmt_size(size):>10}  {plate_path}")
    lines.append(f"  {len(lines)} file(s), {fmt_size(total)} total")
    return "\n".join(lines)
