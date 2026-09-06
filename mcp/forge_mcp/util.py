"""Path handling and result formatting shared by the tools.

Windows is the primary platform (docs/architecture.md ground rules): paths
arrive with backslashes, forward slashes, spaces, `~`, or `%VAR%`, and the
geometry service wants an absolute path. Everything funnels through
:func:`resolve_path`.
"""

from __future__ import annotations

import json
import os
import re
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


def _suggestion_lines(
    data: Mapping[str, Any], indent: str, segment_tool: str = "partforge_segment"
) -> List[str]:
    """The bed_fit failure's segmentation advice, mode object included verbatim.

    `segment_tool` is the tool the suggestion should be handed to, because the
    same check runs for a PARAMS part (partforge_segment) and for an imported
    mesh (segment_model), and naming the wrong one sends the artist to a tool
    that cannot take their object.
    """
    suggestion = data.get("suggested_segmentation")
    if not isinstance(suggestion, Mapping):
        return []
    lines = [f"{indent}suggested segmentation ({suggestion.get('kind', '?')}): "
             f"{suggestion.get('reason', '')}".rstrip()]
    if suggestion.get("feasible") and suggestion.get("mode") is not None:
        mode = json.dumps(suggestion.get("mode"), separators=(", ", ": "))
        lines.append(f"{indent}pass to {segment_tool} verbatim -> mode = {mode}")
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
    # `solid_is_valid` is null for an imported mesh — there is no B-Rep to
    # validate — and "B-Rep valid None" reads like a bug rather than an absence.
    valid = data.get("solid_is_valid")
    valid_text = "n/a (no B-Rep)" if valid is None else fmt_number(valid)
    return (
        f"{verdict}: B-Rep valid {valid_text}, "
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


def _check_lines(
    checks: Any, segment_tool: str = "partforge_segment"
) -> List[str]:
    """One line per check with the numbers that decide it, plus its follow-ups.

    Shared by the two check reports (a PARAMS part and an imported mesh): the
    rows are the same rows, and only the tool the bed_fit suggestion is handed
    to differs.
    """
    lines: List[str] = []
    for check in checks or []:
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
                lines.extend(_suggestion_lines(data, indent, segment_tool))
        elif status != "pass" and check.get("details"):
            lines.append(f"{indent}{check['details']}")
    return lines


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

    lines.extend(_check_lines(checks))
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
    ]
    lines.extend(_segment_table(segments))

    lines.append("")
    lines.append(fmt_plate(payload.get("plate")))
    return "\n".join(lines)


def _segment_table(segments: Sequence[Mapping[str, Any]]) -> List[str]:
    """The pieces, one row each — name, kind, oriented box, counts, watertight.

    Every field is optional: a planning response carries no meshes, and the
    imported-mesh path may report a segment with `stats` it never measured.
    """
    lines = [
        f"  {'segment':<20} {'kind':<9} {'oriented bbox (mm)':<26} {'verts':>7} "
        f"{'faces':>7}  watertight"
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
    return lines


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


# --- Phase 6d reports: imported meshes --------------------------------------
#
# The same two questions as the PartForge pipeline (will it print, how do I cut
# it) asked of a mesh that is already in Blender. The rows are the service's own
# rows, so the check/segment/plate renderers above are reused wholesale; what is
# different is everything around them — the subject is a Blender object rather
# than a script, there are no `overrides` to report, the answer names the
# objects the add-on loaded, and an imported mesh has no B-Rep.


def mesh_subject(object_name: Optional[str], result: Mapping[str, Any]) -> str:
    """What to call the thing that was checked or cut.

    The add-on echoes the object it resolved, which is the useful name when the
    caller omitted `object` and meant "whatever is active".
    """
    reported = str(result.get("object") or "").strip() if isinstance(result, Mapping) else ""
    named = str(object_name or "").strip()
    subject = reported or named
    return f"'{subject}'" if subject else "the active object"


def fmt_mesh_line(payload: Mapping[str, Any], label: str = "mesh") -> Optional[str]:
    """The one line about the geometry that was measured, or None if unreported.

    `stats` is the service's (counts, bbox, watertight) and `mesh` is the
    add-on's (counts plus the metres->millimetres `scale` it applied). Either
    may be absent, so this never assumes both.
    """
    stats = payload.get("stats")
    mesh = payload.get("mesh") if isinstance(payload.get("mesh"), Mapping) else {}
    if isinstance(stats, Mapping) and stats:
        text = fmt_stats(stats)
    elif mesh:
        text = fmt_stats(mesh)
    else:
        return None
    scale = mesh.get("scale")
    if scale is not None:
        # Not places=0: fmt_number strips trailing zeros, which would turn the
        # x1000 metres->millimetres scale into "x1".
        text += f" (scene metres scaled x{fmt_number(scale)} to mm)"
    return f"  {label}: {text}"


def loaded_object_names(objects: Any) -> List[str]:
    """Names out of an `objects` field, whether it holds strings or records.

    `segment_model` reports plain names; `load_meshes` reports
    ``{"object": ..., "vertex_count": ...}`` records, and an add-on written in
    parallel may well settle on either.
    """
    if isinstance(objects, Mapping):
        objects = [objects]
    if isinstance(objects, (str, bytes)):
        objects = [objects]
    if not isinstance(objects, (list, tuple)):
        return []
    names: List[str] = []
    for entry in objects:
        if isinstance(entry, Mapping):
            name = entry.get("object") or entry.get("name")
        else:
            name = entry
        text = str(name).strip() if name is not None else ""
        if text:
            names.append(text)
    return names


def fmt_model_check_report(
    object_name: Optional[str], result: Mapping[str, Any], printer_source: str
) -> str:
    """Print readiness for a mesh the artist imported, rather than a part script."""
    overall = str(result.get("overall", "?")).upper()
    lines = [
        f"Print readiness: {overall} — {mesh_subject(object_name, result)} (imported mesh)",
        f"  {_printer_line(result.get('printer'), printer_source)}",
    ]
    mesh_line = fmt_mesh_line(result)
    if mesh_line:
        lines.append(mesh_line)
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.append("")

    checks = result.get("checks") or []
    if not checks:
        lines.append("  (the add-on returned no checks)")
    else:
        lines.extend(_check_lines(checks, segment_tool="segment_model"))

    lines.append("")
    if result.get("panel"):
        lines.append("  The same rows are now in Blender's Print Checks panel.")
    lines.append(
        "  This is a raw mesh, so there is no B-Rep to validate — 'watertight' "
        "is the triangles' own closedness."
    )
    return "\n".join(lines)


def fmt_model_segment_report(
    object_name: Optional[str],
    result: Mapping[str, Any],
    printer_source: str,
    collection: Optional[str] = None,
) -> str:
    """The cut of an imported mesh: the pieces, the plate, and what is now in Blender."""
    segments = [s for s in (result.get("segments") or []) if isinstance(s, Mapping)]
    pieces = sum(1 for s in segments if s.get("kind") != "hardware")
    hardware = len(segments) - pieces

    lines = [
        f"Cut {mesh_subject(object_name, result)} into {pieces} piece(s)"
        + (f" plus {hardware} printed pin(s)" if hardware else ""),
        f"  mode: {fmt_mode(result.get('mode'))}   "
        f"joint: {fmt_joint(result.get('joint'))}   printer: {printer_source}",
    ]
    mesh_line = fmt_mesh_line(result, label="source mesh")
    if mesh_line:
        lines.append(mesh_line)
    lines.extend(fmt_warnings(result.get("warnings")))

    if segments:
        lines.append("")
        lines.extend(_segment_table(segments))

    plate = result.get("plate")
    lines.append("")
    lines.append(fmt_plate(plate))
    if isinstance(plate, Mapping) and not plate.get("fits", True):
        lines.append(
            "  WARNING: the packed plate does not fit the bed — cut into more "
            "pieces (a bigger radial count, or another planar height) and re-run."
        )

    lines.append("")
    names = loaded_object_names(result.get("objects"))
    where = f" in collection '{collection.strip()}'" if collection and collection.strip() else ""
    if names:
        lines.append(
            f"  Loaded {len(names)} object(s) into Blender{where}, laid out on the plate:"
        )
        lines.append(f"    {', '.join(names)}")
    else:
        count = result.get("count")
        lines.append(
            f"  The add-on reported {count} piece(s) but named no loaded objects "
            "— check the scene with get_scene_info."
            if count
            else "  NOT loaded into Blender: the add-on named no objects."
        )
    return "\n".join(lines)


# --- Phase 3 (RigForge) -----------------------------------------------------

#: Tags are vertex groups prefixed ``tag_`` on the object (docs/architecture.md).
#: The add-on owns that prefix, so the wire carries the BARE name — a model that
#: reaches for "tag_Head" (because it read the contract) gets the same result as
#: one that says "Head".
TAG_PREFIX = "tag_"

#: Blender caps vertex group names at 63 bytes, same as object names.
_MAX_TAG_NAME = 63

#: Retopology face budgets per target platform, taken from
#: templates/character.json (`retopo.target_faces_desktop` / `_mobile`) so the
#: manifest and the tool agree on what "desktop" means.
PLATFORM_TARGET_FACES: Dict[str, int] = {"desktop": 15000, "mobile": 5000}

#: Name suffixes that mark an object as a retopo/LOD derivative of a sculpt.
_DERIVATIVE_SUFFIXES = ("retopo", "lod", "low", "lo")


def normalize_tag_name(raw: Any, *, label: str = "tag") -> str:
    """Clean a tag name and strip the ``tag_`` prefix the add-on adds itself."""
    text = "" if raw is None else str(raw).strip()
    if text.lower().startswith(TAG_PREFIX):
        text = text[len(TAG_PREFIX):].strip()
    if not text:
        raise ForgeError(
            f"No {label} name given. Tags are body parts like 'Head', 'Arm.L' or "
            "'Ear.R' (the tag_ prefix is added by the add-on)."
        )
    if any(ch in text for ch in "\r\n\t"):
        raise ForgeError(f"A {label} name cannot contain line breaks or tabs; got {raw!r}.")
    encoded = text.encode("utf-8")[:_MAX_TAG_NAME]
    return encoded.decode("utf-8", errors="ignore") or text[:_MAX_TAG_NAME]


def normalize_face_indices(faces: Any) -> List[int]:
    """Validate a face-index list: whole, non-negative, de-duplicated, sorted.

    Sorting is not cosmetic — it makes two calls that name the same faces produce
    the same request, which is what lets the request-shaping tests pin the wire.
    """
    if isinstance(faces, (str, bytes)) or not isinstance(faces, (list, tuple, set)):
        raise ForgeError(
            f"`faces` must be a list of face indices like [12, 13, 14]; got {faces!r}."
        )
    seen: set[int] = set()
    for value in faces:
        if isinstance(value, bool):
            raise ForgeError(f"{value!r} is not a face index.")
        if isinstance(value, float):
            if not value.is_integer():
                raise ForgeError(f"Face indices must be whole numbers; got {value}.")
            value = int(value)
        try:
            index = int(str(value).strip() if isinstance(value, str) else value)
        except (TypeError, ValueError) as exc:
            raise ForgeError(f"{value!r} is not a face index.") from exc
        if index < 0:
            raise ForgeError(f"Face indices cannot be negative; got {index}.")
        seen.add(index)
    if not seen:
        raise ForgeError(
            "`faces` was empty. Pass at least one face index, or use "
            "use_selection=true to take Blender's current face selection."
        )
    return sorted(seen)


def fmt_tag_table(tags: Any, subject: str) -> str:
    """`rigforge_list_tags` -> one line per tag with its vertex/face counts."""
    entries = list(tags or []) if isinstance(tags, (list, tuple)) else []
    if not entries:
        return (
            f"No tags on {subject}. Tag body parts with rigforge_tag "
            "(select faces first, or pass explicit face indices)."
        )

    lines = [
        f"{len(entries)} tag(s) on {subject}",
        "",
        f"  {'tag':<24} {'verts':>8} {'faces':>8}",
    ]
    total_verts = 0
    total_faces = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            lines.append(f"  {str(entry):<24}{'?':>9}{'?':>9}")
            continue
        name = normalize_tag_name(entry.get("name", "?"), label="tag") if entry.get("name") else "?"
        verts = entry.get("vertex_count")
        faces = entry.get("face_count")
        total_verts += verts if isinstance(verts, int) and not isinstance(verts, bool) else 0
        total_faces += faces if isinstance(faces, int) and not isinstance(faces, bool) else 0
        lines.append(
            f"  {name:<24.24} {('?' if verts is None else str(verts)):>8} "
            f"{('?' if faces is None else str(faces)):>8}"
        )
    lines.append("")
    lines.append(f"  {'total':<24} {total_verts:>8} {total_faces:>8}")
    return "\n".join(lines)


def retopo_face_counts(result: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """`(name, face_count)` pairs out of a `rigforge_retopo` result.

    The contract sketches ``{"objects": [names], "face_counts": ...}`` without
    pinning the second field's shape, so all three plausible forms are accepted:
    a dict keyed by object name, a list parallel to ``objects``, or ``objects``
    itself being a list of ``{"name", "face_count"}`` records.
    """
    objects = result.get("objects") or []
    counts = result.get("face_counts")

    pairs: List[Tuple[str, Any]] = []
    for index, entry in enumerate(objects):
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or entry.get("object") or f"object {index}")
            faces = entry.get("face_count", entry.get("faces"))
        else:
            name = str(entry)
            faces = None
        if faces is None and isinstance(counts, Mapping):
            faces = counts.get(name)
        elif faces is None and isinstance(counts, (list, tuple)) and index < len(counts):
            faces = counts[index]
        pairs.append((name, faces))
    return pairs


def fmt_baked(baked: Any) -> Optional[str]:
    """One line about a baked normal map, whatever shape the add-on reports it in."""
    if not baked:
        return None
    if isinstance(baked, Mapping):
        bits = []
        for key in ("image", "name"):
            if baked.get(key):
                bits.append(str(baked[key]))
                break
        resolution = baked.get("resolution") or baked.get("size")
        if resolution is not None:
            if isinstance(resolution, (list, tuple)):
                bits.append("x".join(fmt_number(v, 0) for v in resolution) + " px")
            else:
                bits.append(f"{fmt_number(resolution, 0)} px")
        if baked.get("path"):
            bits.append(str(baked["path"]))
        return ", ".join(bits) if bits else json.dumps(dict(baked), separators=(", ", ": "))
    return str(baked)


def fmt_retopo_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    """Created objects and their face counts as a short table."""
    pairs = retopo_face_counts(result)
    lines = [f"Retopologised {subject} — {summary}"]
    if not pairs:
        lines.append("  (the add-on reported no new objects)")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"  {'object':<28} {'faces':>9}")
    for name, faces in pairs:
        lines.append(f"  {name:<28.28} {('?' if faces is None else fmt_number(faces, 0)):>9}")

    baked = fmt_baked(result.get("baked"))
    if baked:
        lines.append("")
        lines.append(f"  normal map baked: {baked}")
    return "\n".join(lines)


def fmt_uv_coverage(value: Any) -> str:
    """Coverage as a percentage, whether the add-on sends 0.78 or 78.0.

    A value at or below 1.0 is read as a fraction (the contract's `uv_coverage`);
    anything larger is already a percentage. 1.0 therefore reads as 100%, which
    is the only sensible meaning either way.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number <= 1.0:
        number *= 100.0
    return f"{number:.1f}%"


def fmt_uv_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    islands = result.get("islands")
    coverage = result.get("uv_coverage", result.get("coverage"))
    parts = [f"{'?' if islands is None else islands} island(s)"]
    if coverage is not None:
        parts.append(f"{fmt_uv_coverage(coverage)} UV coverage")
    seams = result.get("seams")
    if seams is not None:
        parts.append(f"{seams} seam edge(s)")
    return ok(f"unwrapped {subject} ({summary})", ", ".join(parts))


def fmt_manifest(manifest: Any) -> str:
    """A character.json summary — the fields a rigger actually asks about."""
    if not isinstance(manifest, Mapping) or not manifest:
        return "  (no manifest content returned)"
    tags = manifest.get("tags") or []
    retopo = manifest.get("retopo") if isinstance(manifest.get("retopo"), Mapping) else {}
    actions = manifest.get("actions") or []
    notes = str(manifest.get("motion_notes") or "").strip()

    lines = [
        f"  name: {manifest.get('name') or '(unnamed)'}   "
        f"archetype: {manifest.get('archetype') or '(unset)'}",
        f"  tags ({len(tags)}): " + (", ".join(str(t) for t in tags) if tags else "none"),
    ]
    if retopo:
        lines.append(
            f"  retopo: desktop {retopo.get('target_faces_desktop', '?')} / "
            f"mobile {retopo.get('target_faces_mobile', '?')} faces, "
            f"{retopo.get('lods', '?')} LOD(s)"
        )
    if actions:
        lines.append(f"  actions ({len(actions)}): {', '.join(str(a) for a in actions)}")
    if notes:
        lines.append(f"  motion notes: {notes if len(notes) <= 200 else notes[:197] + '...'}")
    return "\n".join(lines)


def fmt_manifest_report(action: str, subject: str, result: Mapping[str, Any],
                        requested_path: Optional[Path]) -> str:
    """What was saved/loaded/read, and where the JSON lives."""
    manifest = result.get("manifest")
    where = result.get("path") or (str(requested_path) if requested_path else None)

    if action == "save":
        head = f"Saved the {subject} manifest"
    elif action == "load":
        head = f"Loaded a manifest onto {subject}"
    else:
        head = f"Manifest for {subject}"
    if where:
        head += f" — {where}"
    elif action == "get":
        head += " (in memory; not written to disk)"

    return "\n".join([head, fmt_manifest(manifest)])


def derivative_objects(scene: Mapping[str, Any], base: str) -> List[str]:
    """Scene objects that look like a retopo/LOD sibling of ``base``.

    ``Hero`` matches ``Hero_retopo``, ``Hero_lod0``, ``Hero_low``; the naming is
    the add-on's, so this is a hint for the status report, never a guarantee.
    """
    if not base:
        return []
    prefix = base.lower() + "_"
    found: List[str] = []
    for entry in scene.get("objects") or []:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "")
        lowered = name.lower()
        if name == base or not lowered.startswith(prefix):
            continue
        if lowered[len(prefix):].startswith(_DERIVATIVE_SUFFIXES):
            found.append(name)
    return found


def scene_object(scene: Mapping[str, Any], name: str) -> Optional[Mapping[str, Any]]:
    for entry in scene.get("objects") or []:
        if isinstance(entry, Mapping) and str(entry.get("name") or "") == name:
            return entry
    return None


# --- Phase 4 (RigForge rig + Godot export) ----------------------------------

#: glTF containers Godot imports directly. Anything else — including a path with
#: no suffix at all — becomes .glb, the single-file form with the meshes,
#: skeleton and animations in one blob.
GLTF_SUFFIXES = (".glb", ".gltf")

#: Armature names that belong to a character without carrying its name: Rigify
#: calls its metarig "metarig" and the rig it generates "RIG-<metarig>".
_GENERIC_METARIG_NAMES = ("metarig",)
_GENERIC_RIG_PREFIXES = ("rig-", "rig_", "rig.")


def normalize_modules(modules: Any) -> List[Dict[str, Any]]:
    """Validate the metarig `modules` list without rewriting the entries.

    The contract sketches ``[{"kind": "limb"|"spine"|"tail"|"chain", "tag": str}]``,
    but the add-on owns that vocabulary and will grow it, so modules cross the
    wire VERBATIM — this only refuses shapes that cannot be a module at all,
    where passing them through turns into an obscure failure inside Blender.
    """
    if isinstance(modules, Mapping):  # one module, unwrapped
        modules = [modules]
    if isinstance(modules, (str, bytes)) or not isinstance(modules, (list, tuple)):
        raise ForgeError(
            "`modules` must be a list of module objects like "
            f'[{{"kind": "limb", "tag": "Arm.L"}}]; got {modules!r}.'
        )
    out: List[Dict[str, Any]] = []
    for entry in modules:
        if not isinstance(entry, Mapping):
            raise ForgeError(
                "Each module must be an object naming what to build and from "
                f'which tag, e.g. {{"kind": "tail", "tag": "Tail"}}; got {entry!r}.'
            )
        out.append(dict(entry))
    if not out:
        raise ForgeError(
            "`modules` was empty. Omit it to let the archetype decide, or list "
            'the modules to add, e.g. [{"kind": "tail", "tag": "Tail"}].'
        )
    return out


def normalize_actions(actions: Any) -> Any:
    """Resolve `actions` into the wire's two forms: ``"all"`` or a name list.

    Forgiving in the same way :func:`normalize_segment_mode` is, because a model
    reaches for whichever of these it thought of first::

        None / "" / "all"          -> "all"
        "idle-loop"                -> ["idle-loop"]
        "idle-loop, walk-loop"     -> ["idle-loop", "walk-loop"]
        ["idle-loop", "walk-loop"] -> unchanged (order kept, duplicates dropped)
    """
    if actions is None:
        return "all"
    if isinstance(actions, (str, bytes)):
        text = str(actions).strip()
        if not text or text.lower() == "all":
            return "all"
        if text.startswith("["):  # a JSON list handed over as a string
            try:
                return normalize_actions(json.loads(text))
            except ValueError as exc:
                raise ForgeError(
                    f"actions looked like a list but did not parse: {exc}"
                ) from exc
        return _action_names(text.split(","))
    if isinstance(actions, (list, tuple, set)):
        return _action_names(actions)
    raise ForgeError(
        f'Could not read actions {actions!r}. Use "all" for every action on the '
        'rig, or a list of names like ["idle-loop", "walk-loop"].'
    )


def _action_names(values: Iterable[Any]) -> List[str]:
    names: List[str] = []
    for value in values:
        if value is None or isinstance(value, (Mapping, list, tuple, set, bool)):
            raise ForgeError(
                f"{value!r} is not an action name. Actions are named strings, e.g. "
                '["idle-loop", "walk-loop"].'
            )
        name = str(value).strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ForgeError(
            'No action names given. Pass "all" to export every action, or a list '
            'like ["idle-loop", "walk-loop"].'
        )
    return names


def warning_texts(warnings: Any) -> List[str]:
    """Warnings as plain strings, whatever shape the add-on reports them in.

    The Phase 4 sketch says ``"warnings"`` and stops there, so a bare string, a
    list of strings and a list of ``{"message", "level"}`` records all render.
    """
    if not warnings:
        return []
    if isinstance(warnings, (str, bytes)):
        text = str(warnings).strip()
        return [text] if text else []
    if isinstance(warnings, Mapping):
        warnings = [warnings]
    if not isinstance(warnings, (list, tuple, set)):
        return [str(warnings)]

    texts: List[str] = []
    for entry in warnings:
        if isinstance(entry, Mapping):
            for key in ("message", "text", "detail", "warning", "reason"):
                if entry.get(key):
                    text = str(entry[key])
                    break
            else:
                text = json.dumps(dict(entry), separators=(", ", ": "))
            level = entry.get("level") or entry.get("severity")
            if level:
                text = f"[{str(level).upper()}] {text}"
        else:
            text = str(entry)
        text = text.strip()
        if text:
            texts.append(text)
    return texts


def fmt_warnings(warnings: Any, indent: str = "  ") -> List[str]:
    """Warnings as their own block, high in the report.

    A rig that generated with "no weights on Ear.R" is the one fact worth
    reading, so warnings never hide at the bottom under a table of file sizes.
    """
    texts = warning_texts(warnings)
    if not texts:
        return []
    lines = [f"{indent}WARNINGS ({len(texts)}):"]
    lines.extend(f"{indent}  ! {text}" for text in texts)
    return lines


def fmt_detail_block(data: Any, indent: str = "    ") -> List[str]:
    """Render a free-form report field (`cleanup_report`, `report`, `changed`).

    The contract pins neither shape, so a dict, a list of dicts, a list of
    strings and a bare scalar all come out as readable lines instead of a repr.
    """
    if data is None or data == "" or data == [] or data == {}:
        return []
    if isinstance(data, Mapping):
        lines: List[str] = []
        for key, value in data.items():
            label = str(key).replace("_", " ")
            if isinstance(value, Mapping):
                inner = ", ".join(f"{k}={fmt_number(v)}" for k, v in value.items())
                lines.append(f"{indent}{label}: {inner or 'none'}")
            elif isinstance(value, (list, tuple)):
                if value and all(isinstance(v, Mapping) for v in value):
                    lines.append(f"{indent}{label} ({len(value)}):")
                    for record in value:
                        lines.append(
                            f"{indent}  "
                            + ", ".join(f"{k}={fmt_number(v)}" for k, v in record.items())
                        )
                else:
                    listed = ", ".join(fmt_number(v) for v in value) if value else "none"
                    lines.append(f"{indent}{label}: {listed}")
            else:
                lines.append(f"{indent}{label}: {fmt_number(value)}")
        return lines
    if isinstance(data, (list, tuple, set)):
        lines = []
        for entry in data:
            if isinstance(entry, Mapping):
                lines.extend(fmt_detail_block(entry, indent))
            else:
                lines.append(f"{indent}{entry}")
        return lines
    return [f"{indent}{fmt_number(data)}"]


def fmt_bone_mapping(mapping: Any, indent: str = "  ") -> List[str]:
    """`{tag: [bones]}` from rigforge_metarig, one line per tag."""
    if not isinstance(mapping, Mapping) or not mapping:
        return []
    lines = [f"{indent}tag -> bones ({len(mapping)}):"]
    for tag, bones in mapping.items():
        if isinstance(bones, (list, tuple, set)):
            listed = ", ".join(str(b) for b in bones) or "(none)"
            count = f"{len(bones)}"
        elif bones is None:
            listed, count = "(none)", "0"
        else:
            listed, count = str(bones), "1"
        lines.append(f"{indent}  {str(tag):<16.16} {count:>3}  {listed}")
    return lines


def fmt_metarig_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    """What was placed, how many bones, which tag drove which bones."""
    name = result.get("metarig") or result.get("object") or "(unnamed)"
    bones = result.get("bone_count", result.get("bones"))
    if isinstance(bones, (list, tuple, set)):
        bones = len(bones)
    lines = [
        f"Metarig placed for {subject} — {summary}",
        f"  metarig: {name}   bones: "
        + ("?" if bones is None else fmt_number(bones, 0)),
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.extend(fmt_bone_mapping(result.get("mapping")))
    lines.append(
        "  next: check the placement in Blender, then rigforge_generate_rig."
    )
    return "\n".join(lines)


def fmt_weighted(weighted: Any) -> str:
    """`weighted` may be a name, a list of names, or just a yes/no."""
    if weighted is None:
        return "not reported"
    if isinstance(weighted, bool):
        return "yes" if weighted else "NO — the mesh was not parented"
    if isinstance(weighted, (list, tuple, set)):
        listed = ", ".join(str(w) for w in weighted)
        return listed or "nothing"
    return str(weighted)


def fmt_rig_report(result: Mapping[str, Any], summary: str) -> str:
    """The generated rig, what it is driving, and the cleanup that ran."""
    rig = result.get("rig") or "(unnamed)"
    lines = [
        f"Rig generated — {summary}",
        f"  rig: {rig}   weighted: {fmt_weighted(result.get('weighted'))}",
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    cleanup = fmt_detail_block(result.get("cleanup_report"))
    if cleanup:
        lines.append("  cleanup:")
        lines.extend(cleanup)
    lines.append(
        "  next: pose and animate, then rigforge_export_godot to bake and write "
        "the glTF."
    )
    return "\n".join(lines)


def fmt_weights_report(action: str, subject: str, result: Mapping[str, Any]) -> str:
    """`report` reads the weights; `cleanup`/`normalize` say what they changed."""
    heads = {
        "report": f"Weights on {subject}",
        "cleanup": f"Cleaned up weights on {subject}",
        "normalize": f"Normalized weights on {subject}",
    }
    lines = [heads.get(action, f"Weights ({action}) on {subject}")]
    lines.extend(fmt_warnings(result.get("warnings")))

    body: List[str] = []
    report = result.get("report")
    if report is not None:
        body.extend(fmt_detail_block(report, "  "))
    changed = result.get("changed")
    if changed is not None:
        if isinstance(changed, (Mapping, list, tuple, set)):
            body.append("  changed:")
            body.extend(fmt_detail_block(changed))
        else:
            body.append(f"  changed: {fmt_number(changed)}")

    if body:
        lines.extend(body)
    else:
        lines.append("  (the add-on reported no detail)")
    return "\n".join(lines)


def fmt_deform_bones(deform: Any) -> str:
    """`deform_bones` may be a count or the list of bone names."""
    if deform is None:
        return "?"
    if isinstance(deform, (list, tuple, set)):
        names = [str(b) for b in deform]
        head = ", ".join(names[:6])
        if len(names) > 6:
            head += f", +{len(names) - 6} more"
        return f"{len(names)}" + (f" ({head})" if head else "")
    return fmt_number(deform, 0)


def fmt_exported_files(files: Any, indent: str = "  ") -> List[str]:
    """The written files with their sizes, from strings or `{name, path}` records."""
    entries = files if isinstance(files, (list, tuple)) else ([files] if files else [])
    lines: List[str] = []
    total = 0
    counted = 0
    for entry in entries:
        if isinstance(entry, Mapping):
            path = entry.get("path") or entry.get("file") or entry.get("name")
            kind = entry.get("kind") or entry.get("type") or ""
        else:
            path, kind = entry, ""
        if not path:
            continue
        size = file_size(path)
        if size is not None:
            total += size
            counted += 1
        lines.append(
            f"{indent}  {str(kind):<10.10} {fmt_size(size) if size is not None else '':>10}"
            f"  {path}"
        )
    if not lines:
        return []
    head = f"{indent}files ({len(lines)})"
    if counted:
        head += f", {fmt_size(total)} on disk"
    return [head + ":"] + lines


def fmt_export_report(
    subject: str, result: Mapping[str, Any], summary: str, requested_path: Any
) -> str:
    """Where it went, what was baked, and every file — warnings first."""
    where = result.get("path") or requested_path
    actions = result.get("actions")
    if isinstance(actions, (list, tuple, set)):
        listed = ", ".join(str(a) for a in actions) or "none"
        count = f"{len(actions)}"
    elif actions is None:
        listed, count = "not reported", "?"
    else:
        listed, count = str(actions), "?"

    lines = [
        f"Exported {subject} to Godot — {where}",
        f"  {summary}",
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.append(f"  deform bones: {fmt_deform_bones(result.get('deform_bones'))}")
    lines.append(f"  actions ({count}): {listed}")
    files = fmt_exported_files(result.get("files"))
    if files:
        lines.extend(files)
    else:
        lines.append(f"  files: (none reported) — expected at least {where}")
    return "\n".join(lines)


def rig_objects(scene: Mapping[str, Any], base: str) -> Dict[str, List[str]]:
    """Armatures in the scene that belong to ``base``, split metarig vs rig.

    Naming is the add-on's, so this is a hint for the status report exactly like
    :func:`derivative_objects`: an armature counts when it carries the character's
    name (``goblin_metarig``, ``goblin_rig``) or when it carries Rigify's own
    default names (``metarig``, ``RIG-metarig``).
    """
    found: Dict[str, List[str]] = {"metarig": [], "rig": []}
    base_lower = (base or "").lower()
    for entry in scene.get("objects") or []:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("type") or "").upper() != "ARMATURE":
            continue
        name = str(entry.get("name") or "")
        lowered = name.lower()
        related = bool(base_lower) and base_lower in lowered
        generic = lowered.startswith(_GENERIC_RIG_PREFIXES) or lowered in (
            "rig",
            *_GENERIC_METARIG_NAMES,
        )
        if not (related or generic):
            continue
        # RIG-metarig is Rigify's GENERATED rig, so the prefix decides before the
        # substring does.
        if lowered.startswith(_GENERIC_RIG_PREFIXES):
            found["rig"].append(name)
        elif "metarig" in lowered:
            found["metarig"].append(name)
        else:
            found["rig"].append(name)
    return found


def next_rig_step(
    *,
    has_tags: bool,
    has_retopo: bool,
    has_metarig: bool,
    has_rig: bool,
    has_actions: Optional[bool] = None,
) -> str:
    """The one next call, walking the pipeline backwards from the far end.

    ``has_actions`` is deliberately three-valued: ``None`` means the add-on could
    not be asked (no Phase 5 commands, or the call failed), and an unknown action
    library must not be reported as an empty one — the export nudge stays, exactly
    as it did before the animation stage existed.
    """
    if has_rig:
        if has_actions is False:
            return (
                "rigforge_action + rigforge_keyframe — open an action and sketch "
                "the motion at a few key frames, or rigforge_retarget to bring in "
                "a user-supplied .bvh/.fbx clip (rigforge_cloth first if the "
                "character wears something)"
            )
        return (
            "rigforge_export_godot — bake the actions onto the deform bones and "
            "write the glTF (plus its Godot import helper)"
        )
    if has_metarig:
        return (
            "rigforge_generate_rig — Rigify generate, then parent the mesh with "
            "automatic weights and run the per-tag cleanup"
        )
    if has_retopo:
        return (
            "rigforge_metarig — place the metarig from the tag landmarks "
            "(rigforge_auto_uv first if the mesh is not unwrapped yet)"
        )
    if has_tags:
        return (
            "rigforge_retopo — rebuild the topology to the face budget, then "
            "rigforge_auto_uv"
        )
    return (
        "rigforge_tag — label the body parts (select the faces first, or pass "
        "explicit face indices)"
    )


# --- Phase 5 (RigForge cloth and animation) ---------------------------------

#: Motion-capture containers Blender imports without any add-on download. The
#: file is the USER's (docs/architecture.md Phase 5: "nothing is fetched"), so an
#: unknown extension is refused rather than corrected — unlike an *output* path,
#: where the tool knows what it is about to write.
MOCAP_SUFFIXES = (".bvh", ".fbx")

#: Godot's looping-clip convention: an action whose name ends `-loop` is imported
#: as a loop. The add-on enforces it when `loop` is true; this is only used to
#: read a loop badge off a name the add-on already settled on.
LOOP_SUFFIX = "-loop"

#: The pose channels one keyframe entry may carry (docs/architecture.md).
KEY_CHANNELS = ("rotation_euler_deg", "location", "scale")

#: Blender caps action names at 63 bytes, same as objects and vertex groups.
_MAX_ACTION_NAME = 63


def normalize_tag_list(tags: Any, *, label: str = "tags") -> List[str]:
    """Clean a list of body-part tag names: de-duplicated, order kept.

    Each name goes through :func:`normalize_tag_name`, so ``"tag_Torso"`` and
    ``"Torso"`` are the same tag here exactly as they are for rigforge_tag. A
    comma-separated string is accepted for the same reason
    :func:`normalize_actions` accepts one — it is what a model reaches for.
    """
    if isinstance(tags, (str, bytes)):
        tags = str(tags).split(",")
    if isinstance(tags, Mapping) or not isinstance(tags, (list, tuple, set)):
        raise ForgeError(
            f'`{label}` must be a list of tag names like ["Torso", "Arm.L"]; '
            f"got {tags!r}."
        )
    names: List[str] = []
    for entry in tags:
        if entry is None or isinstance(entry, (Mapping, list, tuple, set, bool)):
            raise ForgeError(
                f"{entry!r} is not a tag name. Tags are body parts like 'Torso' "
                "or 'Arm.L'; rigforge_list_tags shows the ones this mesh has."
            )
        if not str(entry).strip():
            continue
        name = normalize_tag_name(entry)
        if name not in names:
            names.append(name)
    if not names:
        raise ForgeError(
            f"`{label}` was empty. Name the body-part tags the garment covers, "
            'e.g. ["Torso", "Arm.L", "Arm.R"] — rigforge_list_tags shows what the '
            "mesh has — or use_selection=true to take Blender's face selection."
        )
    return names


def _as_frame(value: Any, where: str) -> int:
    """A timeline frame: whole number, negatives allowed (Blender allows them)."""
    if isinstance(value, bool):
        raise ForgeError(f"{where} `frame` must be a frame number; got {value!r}.")
    if isinstance(value, float):
        if not value.is_integer():
            raise ForgeError(
                f"{where} `frame` must be a whole frame number; got {value}."
            )
        return int(value)
    try:
        return int(str(value).strip() if isinstance(value, str) else value)
    except (TypeError, ValueError) as exc:
        raise ForgeError(
            f"{where} `frame` must be a whole frame number like 1 or 24; got {value!r}."
        ) from exc


def _as_vector3(value: Any, where: str) -> List[float]:
    """One pose channel: exactly three numbers (X, Y, Z)."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ForgeError(
            f"{where} must be three numbers [x, y, z]; got {value!r}."
        )
    if len(value) != 3:
        raise ForgeError(
            f"{where} must be exactly three numbers [x, y, z]; got {len(value)}."
        )
    out: List[float] = []
    for component in value:
        if isinstance(component, bool):
            raise ForgeError(f"{where} must be three numbers [x, y, z]; got {value!r}.")
        try:
            out.append(float(component))
        except (TypeError, ValueError) as exc:
            raise ForgeError(
                f"{where} must be three numbers [x, y, z]; {component!r} is not one."
            ) from exc
    return out


def _normalize_key(entry: Any, index: int) -> Dict[str, Any]:
    where = f"keys[{index}]"
    if not isinstance(entry, Mapping):
        raise ForgeError(
            f"{where} must be an object like "
            '{"bone": "spine.003", "frame": 1, "rotation_euler_deg": [0, 0, 10]}; '
            f"got {entry!r}."
        )
    key = dict(entry)  # unknown fields survive, the way `modules` entries do

    bone = str(key.get("bone") or "").strip()
    if not bone:
        raise ForgeError(
            f"{where} has no `bone`. Every key names the control bone it moves, "
            'e.g. {"bone": "hand_ik.L", "frame": 12, "location": [0, 0, 0.15]}.'
        )
    key["bone"] = bone

    if key.get("frame") is None:
        raise ForgeError(
            f"{where} ('{bone}') has no `frame`. Every key names the frame it "
            "sits on — that is what makes it a key rather than a pose."
        )
    key["frame"] = _as_frame(key.get("frame"), f"{where} ('{bone}')")

    set_channels = []
    for channel in KEY_CHANNELS:
        if key.get(channel) is None:
            key.pop(channel, None)
            continue
        key[channel] = _as_vector3(key[channel], f"{where} ('{bone}') {channel}")
        set_channels.append(channel)
    if not set_channels:
        raise ForgeError(
            f"{where} ('{bone}' at frame {key['frame']}) sets no channel. Give it "
            "at least one of rotation_euler_deg (degrees), location (Blender "
            "units, metres) or scale — a key that changes nothing keyframes "
            "nothing."
        )
    return key


def normalize_keys(keys: Any) -> List[Dict[str, Any]]:
    """Validate the batch of keyframes, entry by entry, before the socket.

    A key must name a bone and a frame and move at least one channel; anything
    else reaches Blender as a silent no-op, which is the worst possible outcome
    for a described-motion pass ("it said it keyed 8 poses and nothing moved").
    Entry order is the caller's — keys are self-describing, so unlike a bare list
    of face indices there is nothing to gain by sorting them.
    """
    if isinstance(keys, Mapping):  # one key, unwrapped
        keys = [keys]
    if isinstance(keys, (str, bytes)) or not isinstance(keys, (list, tuple)):
        raise ForgeError(
            "`keys` must be a list of keyframe objects like "
            '[{"bone": "spine.003", "frame": 1, "rotation_euler_deg": [0, 0, 10]}]; '
            f"got {keys!r}."
        )
    out = [_normalize_key(entry, index) for index, entry in enumerate(keys)]
    if not out:
        raise ForgeError(
            "`keys` was empty. A described-motion pass is a few sketched poses, "
            'e.g. [{"bone": "hand_ik.L", "frame": 1, "location": [0, 0, 0]}, '
            '{"bone": "hand_ik.L", "frame": 12, "location": [0, 0, 0.15]}, '
            '{"bone": "hand_ik.L", "frame": 24, "location": [0, 0, 0]}] — '
            "the interpolation fills in everything between them."
        )
    return out


def keys_frame_range(keys: Sequence[Mapping[str, Any]]) -> Optional[Tuple[int, int]]:
    """(first, last) frame across a normalized key list, for the summary line."""
    frames = [int(key["frame"]) for key in keys if isinstance(key.get("frame"), int)]
    return (min(frames), max(frames)) if frames else None


def normalize_bone_mapping(mapping: Any) -> Any:
    """Resolve `mapping` into the wire's two forms: ``"auto"`` or ``{src: dst}``.

    Same forgiveness as :func:`normalize_actions`: ``None``/``""``/``"auto"`` all
    mean "map by name heuristics and report what did not land", and a JSON object
    handed over as a string is parsed rather than refused.
    """
    if mapping is None:
        return "auto"
    if isinstance(mapping, (str, bytes)):
        text = str(mapping).strip()
        if not text or text.lower() == "auto":
            return "auto"
        if text.startswith("{"):
            try:
                return normalize_bone_mapping(json.loads(text))
            except ValueError as exc:
                raise ForgeError(
                    f"mapping looked like JSON but did not parse: {exc}"
                ) from exc
        raise ForgeError(
            f'Could not read mapping {mapping!r}. Use "auto" to map by name, or '
            'an object from clip bone to rig bone like {"mixamorig:Hips": "torso"}.'
        )
    if isinstance(mapping, Mapping):
        out: Dict[str, str] = {}
        for source, target in mapping.items():
            src = str(source).strip()
            dst = "" if target is None else str(target).strip()
            if not src or not dst or isinstance(target, (Mapping, list, tuple, set)):
                raise ForgeError(
                    "A mapping entry maps one clip bone to one rig bone, e.g. "
                    f'{{"mixamorig:Hips": "torso"}}; got {source!r} -> {target!r}.'
                )
            out[src] = dst
        if not out:
            raise ForgeError(
                '`mapping` was empty. Omit it (or pass "auto") to map by name '
                "heuristics, or list only the bones the heuristics get wrong."
            )
        return out
    raise ForgeError(
        f'Could not read mapping {mapping!r}. Use "auto" to map by name, or an '
        'object from clip bone to rig bone like {"mixamorig:Hips": "torso"}.'
    )


def normalize_retarget_scale(scale: Any) -> Any:
    """``"auto"`` (fit the clip to the rig's proportions) or a positive factor."""
    if scale is None:
        return "auto"
    if isinstance(scale, bool):
        raise ForgeError(f'scale must be "auto" or a positive number; got {scale!r}.')
    if isinstance(scale, (int, float)):
        value = float(scale)
    else:
        text = str(scale).strip()
        if not text or text.lower() == "auto":
            return "auto"
        try:
            value = float(text)
        except ValueError as exc:
            raise ForgeError(
                f'Could not read scale {scale!r}. Use "auto" to fit the clip to '
                "the rig, or a positive multiplier like 0.01."
            ) from exc
    if value <= 0.0:
        raise ForgeError(f"scale must be greater than 0; got {fmt_number(value)}.")
    return value


def normalize_action_name(raw: Any, *, label: str = "action name") -> str:
    """Clean an action name without touching the `-loop` convention.

    The suffix is the add-on's to enforce (docs/architecture.md), so a name
    crosses the wire as typed — the report shows whatever name came back.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ForgeError(
            f"No {label} given. Actions are named clips like 'idle-loop', "
            "'walk-loop', 'jump' or 'attack'."
        )
    if any(ch in text for ch in "\r\n\t"):
        raise ForgeError(
            f"An {label} cannot contain line breaks or tabs; got {raw!r}."
        )
    encoded = text.encode("utf-8")[:_MAX_ACTION_NAME]
    return encoded.decode("utf-8", errors="ignore") or text[:_MAX_ACTION_NAME]


def action_name_for_clip(path: Path) -> str:
    """Default action name for a retarget: the clip file's own stem."""
    stem = str(path.stem).strip()
    if not stem:
        return "mocap"
    encoded = stem.encode("utf-8")[:_MAX_ACTION_NAME]
    return encoded.decode("utf-8", errors="ignore") or "mocap"


def action_names(actions: Any) -> List[str]:
    """Names out of an `actions` list of strings or of `{name, loop, ...}` records."""
    if actions is None:
        return []
    if isinstance(actions, (str, bytes)):
        text = str(actions).strip()
        return [text] if text else []
    if isinstance(actions, Mapping):
        actions = [actions]
    if not isinstance(actions, (list, tuple, set)):
        return [str(actions)]
    names: List[str] = []
    for entry in actions:
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or entry.get("action") or "").strip()
        else:
            name = str(entry).strip()
        if name:
            names.append(name)
    return names


def fmt_frame_range(value: Any) -> str:
    """`[1, 24]`, `{"start": 1, "end": 24}`, `24` or `"1-24"` -> `1-24`."""
    if value is None:
        return "?"
    if isinstance(value, Mapping):
        start = value.get("start", value.get("first"))
        end = value.get("end", value.get("last"))
        if start is None and end is None:
            return json.dumps(dict(value), separators=(", ", ": "))
        return f"{fmt_number(start, 0)}-{fmt_number(end, 0)}"
    if isinstance(value, (list, tuple)):
        parts = [fmt_number(v, 0) for v in value]
        if len(parts) == 2:
            return f"{parts[0]}-{parts[1]}"
        return ", ".join(parts) if parts else "?"
    return fmt_number(value, 0)


def fmt_name_list(values: Any, limit: int = 8) -> str:
    """`count: a, b, c, +n more` from a list, a `{src: dst}` mapping or a count.

    Every Phase 5 result field that could be "how many" or "which ones"
    (`mapped`, `unmapped`, `shape_keys`) arrives through here, because the
    contract pins none of their shapes.
    """
    if values is None:
        return "not reported"
    if isinstance(values, bool):
        return "yes" if values else "no"
    if isinstance(values, int):
        return str(values)
    if isinstance(values, float):
        return fmt_number(values, 0)
    if isinstance(values, (str, bytes)):
        text = str(values).strip()
        return text or "none"
    if isinstance(values, Mapping):
        entries = [f"{key} -> {value}" for key, value in values.items()]
    elif isinstance(values, (list, tuple, set)):
        entries = []
        for entry in values:
            if isinstance(entry, Mapping):
                source = entry.get("source") or entry.get("src") or entry.get("name")
                target = entry.get("target") or entry.get("dst") or entry.get("bone")
                if source and target:
                    entries.append(f"{source} -> {target}")
                elif source or target:
                    entries.append(str(source or target))
                else:
                    entries.append(json.dumps(dict(entry), separators=(", ", ": ")))
            else:
                entries.append(str(entry))
    else:
        return str(values)

    if not entries:
        return "0"
    shown = ", ".join(entries[:limit])
    if len(entries) > limit:
        shown += f", +{len(entries) - limit} more"
    return f"{len(entries)}: {shown}"


def fmt_counted(label: str, values: Any, limit: int = 8) -> str:
    """`mapped (2): a -> b, c -> d`, or `mapped: 22` when only a count came back."""
    listed = fmt_name_list(values, limit)
    head, separator, rest = listed.partition(": ")
    if separator:
        return f"{label} ({head}): {rest}"
    return f"{label}: {listed}"


def fmt_shape_keys(shape_keys: Any) -> Optional[str]:
    """The `shape_keys` line of a cloth report, or None when there are none."""
    if shape_keys is None or shape_keys == [] or shape_keys == {} or shape_keys == "":
        return None
    if fmt_name_list(shape_keys) in ("0", "none", "not reported"):
        return None
    return fmt_counted("shape keys", shape_keys)


def fmt_cloth_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    """The garment, how it is driven, and the shape keys it left behind."""
    garment = result.get("garment") or result.get("object") or "(unnamed)"
    output = result.get("output") or "(not reported)"
    lines = [
        f"Garment made from {subject} — {summary}",
        f"  garment: {garment}   output: {output}",
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    keys = fmt_shape_keys(result.get("shape_keys"))
    if keys:
        lines.append(f"  {keys}")
    lines.append(
        "  next: rigforge_action to open an action, then rigforge_keyframe "
        "(or rigforge_retarget) to move it."
    )
    return "\n".join(lines)


def fmt_action_table(actions: Any, indent: str = "  ") -> List[str]:
    """The action library: one row per action with its loop badge and frames."""
    entries = actions if isinstance(actions, (list, tuple)) else ([actions] if actions else [])
    rows: List[Tuple[str, bool, Any, Any]] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or entry.get("action") or "?")
            loop = entry.get("loop")
            frames = entry.get("frames", entry.get("frame_range"))
            nla = entry.get("nla", entry.get("pushed", entry.get("track")))
        else:
            name, frames, nla, loop = str(entry), None, None, None
        if loop is None:  # not reported: the `-loop` suffix is the convention
            loop = name.endswith(LOOP_SUFFIX)
        rows.append((name, bool(loop), frames, nla))

    if not rows:
        return []
    lines = [f"{indent}{'action':<28} {'loop':<5} {'frames':<12} nla"]
    for name, loop, frames, nla in rows:
        if nla is None:
            track = "-"
        elif isinstance(nla, bool):
            track = "yes" if nla else "-"
        else:
            track = str(nla)
        lines.append(
            f"{indent}{name:<28.28} {('yes' if loop else '-'):<5} "
            f"{fmt_frame_range(frames):<12.12} {track:.20}"
        )
    return lines


_ACTION_HEADS = {
    "new": "Action created",
    "list": "Action library",
    "delete": "Action deleted",
    "duplicate": "Action duplicated",
    "rename": "Action renamed",
    "push_nla": "Action pushed to an NLA track",
}


def fmt_action_report(action: str, result: Mapping[str, Any], summary: str) -> str:
    """What the call did, then the whole library as it now stands."""
    head = _ACTION_HEADS.get(action, f"Action ({action})")
    lines = [f"{head} — {summary}"]
    lines.extend(fmt_warnings(result.get("warnings")))

    actions = result.get("actions")
    table = fmt_action_table(actions)
    if table:
        lines.append("")
        lines.extend(table)
        lines.append("")
        lines.append(f"  {len(action_names(actions))} action(s)")
    elif action == "list":
        lines.append(
            "  no actions yet — rigforge_action(action='new', name='idle-loop', "
            "loop=true), then sketch the motion with rigforge_keyframe or import "
            "a clip with rigforge_retarget."
        )
    else:
        lines.append("  (the add-on reported no action library)")
    return "\n".join(lines)


def fmt_keyframe_report(result: Mapping[str, Any], summary: str, requested: str) -> str:
    """How many keys landed, and over what span of the timeline."""
    named = str(result.get("action") or "").strip()
    action = f"'{named}'" if named else requested
    keys_set = result.get("keys_set", result.get("keys"))
    lines = [
        f"Keyframed {action} — {summary}",
        f"  {fmt_counted('keys set', keys_set)}   "
        f"frames {fmt_frame_range(result.get('frame_range'))}",
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.append(
        "  next: scrub it in Blender; more keys refine the same action, and "
        "rigforge_export_godot bakes it for Godot."
    )
    return "\n".join(lines)


def fmt_retarget_report(
    source: Any, subject: str, result: Mapping[str, Any], summary: str
) -> str:
    """The baked action, and — loudly — the bones that did not map."""
    action = result.get("action") or "(unnamed)"
    frames = result.get("frames", result.get("frame_range"))
    lines = [
        f"Retargeted {source} onto {subject} — {summary}",
        f"  action: {action}   frames: {fmt_frame_range(frames)}",
    ]
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.append("  " + fmt_counted("mapped", result.get("mapped")))

    unmapped = result.get("unmapped")
    if unmapped in (None, [], {}, 0, ""):
        lines.append("  unmapped: none — every source bone found a home")
    else:
        lines.append("  " + fmt_counted("UNMAPPED", unmapped, limit=12))
        lines.append(
            "    those bones drive nothing. Re-run with an explicit `mapping` for "
            "them if the motion looks wrong."
        )
    lines.append(
        "  next: scrub the action in Blender, then rigforge_export_godot."
    )
    return "\n".join(lines)


# --- Phase 7 (making new parts) ---------------------------------------------

#: Long enough for "small-magnet-holder-with-countersunk-screw-holes", short
#: enough to stay a readable folder name on Windows.
PROJECT_SLUG_MAX = 60

#: Runs of anything that is not a lowercase letter or digit collapse to one dash.
_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")

#: Characters that can only be an attempt to leave projects/ (or a Windows drive
#: reference). Slugging would silently swallow them, and silently writing
#: somewhere else is the one failure mode worth refusing loudly.
_TRAVERSAL_MARKERS = ("/", "\\", "..", ":", "\x00")


def project_slug(name: Any) -> str:
    """`"a small Magnet Holder!"` -> `"small-magnet-holder"`, or refuse.

    The slug is a single folder name under ``projects/``. Anything that looks
    like a path — a separator, ``..``, a drive letter — is refused rather than
    cleaned, because a caller who wrote one meant a location, and quietly
    writing somewhere else is worse than an error.
    """
    raw = "" if name is None else str(name).strip().strip('"').strip()
    if not raw:
        raise ForgeError(
            "No part name given. Name the thing in plain words — "
            '"small magnet holder" — and it becomes projects/small-magnet-holder/.'
        )
    for marker in _TRAVERSAL_MARKERS:
        if marker in raw:
            raise ForgeError(
                f"{name!r} is not a part name — it looks like a path "
                f"(it contains {marker!r}). New parts are always written to "
                "projects/<name>/part.py, so pass just the name, e.g. "
                '"small magnet holder".'
            )
    if raw.startswith("~") or raw.startswith("%") or raw.startswith("$"):
        raise ForgeError(
            f"{name!r} is not a part name — it looks like a path or an "
            'environment variable. Pass just the name, e.g. "small magnet holder".'
        )

    slug = _SLUG_SEPARATORS.sub("-", raw.lower()).strip("-")
    if not slug:
        raise ForgeError(
            f"{name!r} has no letters or digits in it, so it cannot name a "
            'folder. Try something like "small magnet holder".'
        )
    if len(slug) > PROJECT_SLUG_MAX:
        slug = slug[:PROJECT_SLUG_MAX].rstrip("-")
    return slug


def projects_root() -> Path:
    """The one directory new parts may be written under."""
    return Path(config.PROJECTS_DIR).expanduser().resolve()


def project_paths(slug: str) -> Tuple[Path, Path, Path]:
    """``(folder, part.py, spec.json)`` for a slug, checked to stay in projects/.

    The containment check is belt and braces on top of :func:`project_slug` —
    two independent guards, because this is the only tool in the server that
    writes source code to disk.
    """
    root = projects_root()
    folder = (root / slug).resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        raise ForgeError(
            f"{slug!r} would write outside {root}. New parts only ever land in "
            "projects/<name>/."
        ) from None
    return folder, folder / "part.py", folder / "spec.json"


def normalize_script_source(source: Any) -> str:
    """The script text as it will be written: LF endings, one trailing newline."""
    if source is None or not isinstance(source, str) or not source.strip():
        raise ForgeError(
            "No script_source given. Pass the whole Python file: a top-level "
            "PARAMS dict and a build(p) that returns a Build123d part "
            "(docs/part-authoring.md, service/samples/ring_band.py)."
        )
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    return text if text.endswith("\n") else text + "\n"


def spec_document(
    name: str,
    slug: str,
    params: Any,
    *,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """A minimal spec.json for a new part, shaped like templates/spec.json.

    The parameters are mirrored from the schema the service just resolved, so
    the spec and the script cannot disagree on the day they are written.
    """
    mirrored: Dict[str, Any] = {}
    if isinstance(params, Mapping):
        for key, spec in params.items():
            if not isinstance(spec, Mapping):
                mirrored[str(key)] = {"value": spec}
                continue
            entry: Dict[str, Any] = {}
            for field in ("value", "unit", "min", "max", "step", "description"):
                if spec.get(field) is not None:
                    entry[field] = spec[field]
            mirrored[str(key)] = entry

    return {
        "_comment": (
            "Contract between you and Claude for this part. Claude regenerates "
            "part.py from this; edit freely. Created by partforge_new_part."
        ),
        "name": slug,
        "description": description or (
            f"{name} — written by the Forge Assistant. Replace this line with what "
            "the part is for, what it fits, and anything it has to clear."
        ),
        "reference_images": [],
        "parameters": mirrored,
        "features": [],
        "print": {
            "printer": config.SPEC_PRINTER_REF,
            "segments": "auto",
            "joint_type": "dovetail",
            "mold_mode": False,
        },
        "script": "part.py",
        "exports": [],
    }


def fmt_new_part_report(
    *,
    name: str,
    slug: str,
    script: Path,
    params: Any,
    created: bool,
    spec: Optional[Path],
    spec_created: bool,
) -> str:
    """What was written, and the sliders the artist just gained."""
    verb = "Created" if created else "Updated"
    count = len(params) if isinstance(params, Mapping) else 0
    lines = [
        f"{verb} {slug} — {script}",
        f"  {count} parameter(s) validated by the geometry service:",
        fmt_params(params),
    ]
    if spec is not None:
        lines.append(
            f"  spec.json {'written' if spec_created else 'left as it was'}: {spec}"
        )
    lines.append(
        f"  next: partforge_open_in_panel('{script}') to put the sliders in the "
        "Forge panel, partforge_generate to build it, then partforge_check — "
        "always check before calling it done."
    )
    return "\n".join(lines)


def fmt_open_report(script: Path, result: Mapping[str, Any]) -> str:
    """The panel is now pointed at this script, with N sliders on it."""
    count = result.get("param_count")
    names = result.get("params")
    lines = [
        f"Opened {script.name} in the Blender Forge panel — {script}",
        f"  {'?' if count is None else count} slider(s) ready under "
        "View3D sidebar (N) > Forge > PartForge",
    ]
    if isinstance(names, (list, tuple)) and names:
        lines.append("  parameters: " + ", ".join(str(n) for n in names))
    if result.get("object"):
        lines.append(f"  the panel will build/replace the object '{result['object']}'")
    lines.append(
        "  next: partforge_generate on the same script so the part is actually "
        "visible in the viewport — opening the panel builds nothing."
    )
    return "\n".join(lines)


# --- Phase 6c (reference images) --------------------------------------------

#: What Blender will open as a reference AND Claude Code's Read tool renders.
#: The add-on and the assistant bridge check the same five, on purpose: a file
#: the artist could attach to a message is a file this tool can put in the
#: viewport, with no "that one only works over there".
REFERENCE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

#: The three orthographic views a reference can be placed in.
REFERENCE_VIEWS = ("front", "side", "top")

#: Default longer side of a reference, in millimetres (docs/architecture.md).
REFERENCE_SIZE_MM = 200.0


def reference_path(raw: str) -> Path:
    """An image on disk, or a refusal that says which formats work."""
    path = resolve_path(raw, label="image path")
    if path.is_dir():
        raise ForgeError(f"{path} is a folder, not an image file.")
    if path.suffix.lower() not in REFERENCE_SUFFIXES:
        raise ForgeError(
            f"{path.name} is not an image Forge can load. Reference images are "
            + ", ".join(REFERENCE_SUFFIXES)
            + " — re-save it as one of those."
        )
    if not path.is_file():
        raise ForgeError(f"No file at {path} (resolved from {raw!r}).")
    return path


def fmt_reference_report(path: Path, view: str, result: Mapping[str, Any]) -> str:
    """Where the picture landed, and that it is an ordinary object now."""
    name = result.get("object") or "the reference"
    width = result.get("width_mm")
    height = result.get("height_mm")
    lines = [
        f"Loaded {path.name} into the viewport as '{name}' "
        f"({view} view, {fmt_number(width, 1)} x {fmt_number(height, 1)} mm)."
    ]
    if result.get("replaced"):
        lines.append(f"  replaced the reference already called '{name}'")
    lines.append(
        "  it is a half-transparent image plane sitting just behind the origin, "
        f"facing the {view} view (Numpad "
        + {"front": "1", "side": "3", "top": "7"}.get(view, "1")
        + ")"
    )
    lines.append(
        f"  tell the artist they can move, scale or hide '{name}' like any other "
        "object (click it and press G, or the eye icon in the Outliner) — it is "
        "an empty, so it can never end up in an export"
    )
    lines.append(
        "  it is a reference to measure against, not something to trace: build "
        "the model from parameters."
    )
    return "\n".join(lines)


# --- Previews (the assistant's eyes) -----------------------------------------

#: The views render_preview can take. The last three are deliberately the same
#: three `load_reference` uses, so a `front` render and a `front` reference are
#: the same projection and can be held up against each other.
PREVIEW_VIEWS = ("iso", "front", "side", "top")

#: Square pixels. 768 is enough to judge a silhouette; the floor and ceiling
#: match the add-on's so a refusal reads the same on either side.
PREVIEW_RESOLUTION = 768
PREVIEW_MIN_RESOLUTION = 128
PREVIEW_MAX_RESOLUTION = 2048

#: Bumped per render inside one server process, so consecutive previews are
#: preview-001, preview-002 ... rather than one file overwritten. Comparing a
#: change against the render before it needs both files to still exist.
_preview_counter = 0


def preview_path(view: str, objects: Sequence[str] | None = None) -> Path:
    """A fresh scratch .png to render into, its folder already made."""
    global _preview_counter

    _preview_counter += 1
    stem = f"preview-{_preview_counter:03d}-{view}"
    names = [str(n).strip() for n in (objects or []) if str(n).strip()]
    if len(names) == 1:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", names[0]).strip("-")[:40]
        if slug:
            stem = f"{stem}-{slug}"
    path = Path(config.PREVIEWS_DIR) / f"{stem}.png"
    ensure_parent_dir(path)
    return path


def normalize_preview_resolution(resolution: Any) -> int:
    """A square pixel size, or a refusal that names the range."""
    if resolution is None:
        return PREVIEW_RESOLUTION
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)):
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution!r})."
        )
    if isinstance(resolution, float) and not float(resolution).is_integer():
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution})."
        )
    value = int(resolution)
    if not PREVIEW_MIN_RESOLUTION <= value <= PREVIEW_MAX_RESOLUTION:
        raise ForgeError(
            f"resolution must be between {PREVIEW_MIN_RESOLUTION} and "
            f"{PREVIEW_MAX_RESOLUTION} pixels (got {value}). The default, "
            f"{PREVIEW_RESOLUTION}, is right for almost everything."
        )
    return value


def normalize_preview_objects(objects: Any) -> list[str]:
    """The names to frame, or an empty list meaning 'everything visible'."""
    if objects is None:
        return []
    if isinstance(objects, str):
        objects = [objects]
    if not isinstance(objects, (list, tuple)):
        raise ForgeError("objects must be a list of Blender object names.")
    names: list[str] = []
    for entry in objects:
        if not isinstance(entry, str) or not entry.strip():
            raise ForgeError(
                f"objects must be a list of object names; {entry!r} is not one."
            )
        if entry.strip() not in names:
            names.append(entry.strip())
    return names


def fmt_preview_report(result: Mapping[str, Any]) -> str:
    """Where the picture is, and the instruction to go and look at it.

    The path is on its own line and repeated in the instruction because this
    report has exactly one job: get the model to Read the file. A preview
    nobody looked at is worse than no preview — it is the same blind design
    with an extra tool call in front of it.
    """
    path = str(result.get("path") or "")
    names = [str(n) for n in (result.get("objects") or [])]
    view = str(result.get("view") or "iso")
    resolution = result.get("resolution")

    if not names:
        subject = "the scene"
    elif len(names) <= 4:
        subject = ", ".join(names)
    else:
        subject = f"{', '.join(names[:4])} and {len(names) - 4} more"

    size = result.get("bounds_mm", {})
    size = size.get("size") if isinstance(size, Mapping) else None

    lines = [
        f"Rendered {subject} — {view} view, {fmt_number(resolution)} px"
        + (f", {_dims(size, 1)} overall" if size else "")
        + ".",
        "",
        f"    {path}",
        "",
        "READ THAT FILE NOW. It is the picture of what you just made, and it is "
        "the only way you will know whether it looks right — checks pass on "
        "shapes that are stiff, sparse and flat.",
    ]

    if names and len(names) > 1:
        lines.append(f"  it frames {len(names)} objects together, so what you see "
                     "is how they read as one thing")
    if result.get("framed_all_visible"):
        lines.append("  every visible mesh is in frame; pass `objects` to look at "
                     "one part on its own")

    lines.append(
        "  when you look: is the DENSITY right (too few leaves, too few teeth, "
        "too sparse a band)? Are the PROPORTIONS the reference's proportions? "
        "Does the SILHOUETTE read as the thing it is meant to be? Is it as SOFT "
        "or as sharp as the reference?"
    )
    lines.append(
        "  if you were given a reference picture, Read that too and compare them "
        "side by side. If it visibly misses, change the parameters and render "
        "again — do not describe a shape you have not looked at."
    )

    notes = result.get("notes") or []
    for entry in notes:
        lines.append(f"  note: {entry}")
    if str(result.get("shading")) == "material":
        lines.append("  this one is material shading (EEVEE), so colours and "
                     "materials are what you see")
    return "\n".join(lines)


# --- Phase 8: the workspace copilot ------------------------------------------

#: Bumped per capture, like the preview counter and for the same reason: the
#: point of a check-in is comparing this look against the last one.
_checkin_counter = 0


def checkin_path(kind: str, suffix: str = ".png") -> Path:
    """A fresh scratch file for one piece of check-in evidence."""
    global _checkin_counter

    _checkin_counter += 1
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(kind)).strip("-") or "checkin"
    path = Path(config.PREVIEWS_DIR) / f"checkin-{_checkin_counter:03d}-{slug}{suffix}"
    ensure_parent_dir(path)
    return path


def normalize_axes(axes: Any) -> Any:
    """Validate the `axes` argument of `set_overlays`, or refuse by name.

    Accepts a list of x/y/z, the word "all", or a bool — the same three forms
    the add-on takes — and refuses anything else HERE, so a typo costs a
    sentence rather than a round trip to Blender.
    """
    if axes is None or isinstance(axes, bool):
        return axes
    if isinstance(axes, str):
        text = axes.strip().lower()
        if text in {"all", "xyz", "none", ""}:
            return text
        axes = list(text.replace(",", " ").split()) or list(text)
    if not isinstance(axes, (list, tuple)):
        raise ForgeError(
            'axes must be a list like ["x", "y", "z"], the word "all", or '
            f"true/false (got {axes!r})."
        )
    out = []
    for entry in axes:
        letter = str(entry).strip().lower().lstrip("+")
        if letter not in {"x", "y", "z"}:
            raise ForgeError(
                f'axes entries must be "x", "y" or "z" (got {entry!r}).'
            )
        if letter not in out:
            out.append(letter)
    return out


def fmt_workspace_report(result: Mapping[str, Any], extra: Sequence[str] = ()) -> str:
    """The one shape every workspace tool answers in: what changed, and where.

    Both halves are the point. "Done" alone leaves the artist exactly as
    dependent as they were; the `where` line is the difference between doing
    something for someone and teaching them while you do it. The model is told,
    in the last line, to pass the pair on in ONE sentence — because a nine-step
    tutorial is the bug this whole phase exists to fix.
    """
    changed = str(result.get("changed") or "Done.")
    where = str(result.get("where") or "")
    lines = [changed]
    if where:
        lines.append(f"  where the artist would do it themselves: {where}")
    viewports = result.get("viewports")
    if isinstance(viewports, int) and viewports > 1:
        lines.append(f"  applied to all {viewports} open 3D viewports")
    for entry in extra:
        if entry:
            lines.append(f"  {entry}")
    lines.append(
        "  Tell them in ONE line: what changed, then where the switch lives. "
        "Do not write out the steps — you already did it."
    )
    return "\n".join(lines)


def _place(location: Any) -> str:
    values = [fmt_number(v, 1) for v in (location or [])]
    return "(" + ", ".join(values) + ") mm" if values else "somewhere"


def fmt_diagnose_report(result: Mapping[str, Any]) -> str:
    """`mesh_diagnose` as the numbers a teacher would actually quote.

    Ordered worst-first and every entry carries a place, because "there is some
    self-intersection" is not a note an artist can act on and "the left ear
    passes through the head at (-42, 18, 96) mm" is.
    """
    name = result.get("object") or "the mesh"
    lines = [
        f"{name}: {fmt_number(result.get('face_count'), 0)} faces, "
        f"{fmt_number(result.get('vertex_count'), 0)} vertices"
        + (" (modifiers applied)" if result.get("evaluated") else ""),
        "",
    ]
    for sentence in result.get("verdict") or []:
        lines.append(f"  {sentence}")

    clip = result.get("self_intersections") or {}
    for example in (clip.get("examples") or [])[:5]:
        lines.append(f"    clipping at {_place(example.get('location_mm'))}")
    topo = result.get("topology") or {}
    for example in (topo.get("edge_examples") or [])[:5]:
        lines.append(f"    {example.get('kind')} at "
                     f"{_place(example.get('location_mm'))}")
    density = result.get("density") or {}
    for entry in (density.get("starved") or [])[:3]:
        lines.append(f"    starved: {entry.get('faces')} faces around "
                     f"{_place(entry.get('location_mm'))}, about "
                     f"{fmt_number(entry.get('times_median'), 1)}x coarser — "
                     "remesh there")
    for entry in (density.get("dense") or [])[:3]:
        lines.append(f"    crammed: {entry.get('faces')} faces around "
                     f"{_place(entry.get('location_mm'))}, about "
                     f"{fmt_number(entry.get('times_median'), 1)}x denser")
    for example in ((result.get("ngons") or {}).get("examples") or [])[:3]:
        lines.append(f"    {example.get('sides')}-sided face at "
                     f"{_place(example.get('location_mm'))}")

    ngons = result.get("ngons") or {}
    if ngons.get("count"):
        lines.append(f"  {ngons['count']} faces have more than four sides "
                     f"(largest {ngons.get('max_sides')})")
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    if result.get("clean"):
        lines.append("  Nothing to fix numerically — say so briefly and let "
                     "them get back to work.")
    else:
        lines.append("  Name at most THREE of these, each with its place and "
                     "its fix. Offer to do the ones a tool can do.")
    return "\n".join(lines)


def fmt_check_in_report(images: Sequence[tuple], diagnose: Any,
                        scene: Any, notes: Sequence[str] = ()) -> str:
    """Everything `check_my_work` gathered, with the instruction to LOOK first."""
    lines = ["Here is everything to look at before you say anything.", ""]
    for label, path in images:
        lines.append(f"  {label}:")
        lines.append(f"    {path}")
    if images:
        lines.append("")
        lines.append("READ %s NOW with the Read tool. A critique written "
                     "without looking is a guess."
                     % ("BOTH FILES" if len(images) > 1 else "THAT FILE"))
        lines.append("")
    if scene:
        lines.append("--- Where they are working ---")
        lines.append(str(scene).rstrip())
        lines.append("")
    if diagnose:
        lines.append("--- Mesh check ---")
        lines.append(str(diagnose).rstrip())
        lines.append("")
    for note in notes:
        lines.append(f"  could not gather: {note}")
    lines.append(
        "Then answer as a teacher: one clause on what is working, then at most "
        "three concrete things with WHERE and the fix. Short — they are mid-"
        "stroke."
    )
    return "\n".join(lines)


# --- Phase 6b (flows) --------------------------------------------------------

#: Blender socket commands a flow step may call — the command registry from
#: docs/architecture.md, mirrored here so `flow_save` can refuse a typo before
#: it is written rather than at run time, with Blender not even required.
#: `flow_list`/`flow_run` are deliberately absent: flows do not nest.
KNOWN_BLENDER_OPS = frozenset({
    # scene + health
    "ping", "get_scene_info", "execute_python",
    # common mesh operations
    "symmetrize", "mirror", "remesh", "decimate", "shade", "apply_transforms",
    "set_origin", "boolean", "merge_by_distance", "separate_loose",
    # objects
    "select_object", "rename_object", "delete_object", "export_stl",
    # references (Phase 6c)
    "load_reference",
    # looking at the result — a flow can end by leaving a picture on disk
    "render_preview",
    # the workspace copilot (Phase 8): a flow can end by putting the artist in
    # the right mode, looking at the right angle, with the grid on
    "set_view", "frame_object", "local_view", "set_shading", "set_overlays",
    "set_mode", "sculpt_brush", "capture_viewport", "mesh_diagnose",
    # PartForge
    "load_mesh", "load_meshes", "partforge_open",
    # imported meshes (Phase 6d)
    "check_model", "segment_model",
    # generated meshes (Phase 7 — meshgen writes a .glb, this brings it in)
    "import_generated",
    # RigForge
    "rigforge_list_tags", "rigforge_tag", "rigforge_untag", "rigforge_manifest",
    "rigforge_retopo", "rigforge_auto_uv", "rigforge_status", "rigforge_metarig",
    "rigforge_generate_rig", "rigforge_weights", "rigforge_export_godot",
    "rigforge_cloth", "rigforge_action", "rigforge_keyframe", "rigforge_retarget",
})

#: Geometry-service endpoints a flow step may call (docs/architecture.md).
KNOWN_SERVICE_OPS = frozenset({
    "/health", "/parse_params", "/generate", "/export", "/check", "/segment",
    "/export_segments", "/slice", "/mold", "/export_mold",
    # raw-mesh input (Phase 6d): the same two answers for a downloaded model
    "/check_mesh", "/segment_mesh",
})

FLOW_NAME_MAX = 60


def flow_slug(name: Any) -> str:
    """`"Segment Into 4!"` -> `"segment-into-4"`, or refuse.

    Exactly the rules :func:`project_slug` uses, for exactly the same reason:
    the slug is a single filename under ``flows/`` and anything path-shaped is
    an error rather than something to be quietly cleaned up.
    """
    raw = "" if name is None else str(name).strip().strip('"').strip()
    if raw.lower().endswith(".json"):
        raw = raw[: -len(".json")]
    if not raw:
        raise ForgeError(
            'No flow name given. Name it for what it does — "segment into 4" — '
            "and it becomes flows/segment-into-4.json."
        )
    for marker in _TRAVERSAL_MARKERS:
        if marker in raw:
            raise ForgeError(
                f"{name!r} is not a flow name — it looks like a path (it contains "
                f"{marker!r}). Flows are always written to flows/<name>.json, so "
                'pass just the name, e.g. "segment into 4".'
            )
    if raw.startswith("~") or raw.startswith("%") or raw.startswith("$"):
        raise ForgeError(
            f"{name!r} is not a flow name — it looks like a path or an "
            'environment variable. Pass just the name, e.g. "segment into 4".'
        )
    slug = _SLUG_SEPARATORS.sub("-", raw.lower()).strip("-")
    if not slug:
        raise ForgeError(
            f"{name!r} has no letters or digits in it, so it cannot name a file. "
            'Try something like "segment into 4".'
        )
    if len(slug) > FLOW_NAME_MAX:
        slug = slug[:FLOW_NAME_MAX].rstrip("-")
    return slug


def flows_root() -> Path:
    """The one directory flows are read from and written to."""
    return Path(config.FLOWS_DIR).expanduser().resolve()


def flow_path(slug: str) -> Path:
    """``flows/<slug>.json``, checked to stay inside flows/."""
    root = flows_root()
    path = (root / f"{slug}.json").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ForgeError(
            f"{slug!r} would write outside {root}. Flows only ever land in "
            "flows/<name>.json."
        ) from None
    return path


def _json_safe(value: Any, where: str) -> Any:
    """Everything in a flow has to survive a round trip through the file."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ForgeError(
            f"{where} is not JSON-serialisable ({exc}). A flow is a file: every "
            "argument has to be a string, number, boolean, list or object."
        ) from exc
    return value


def normalize_flow_params(params: Any) -> Dict[str, Any]:
    """`{name: {"value", "unit"?, "description"?}}`, with bare values allowed.

    Passing `{"wedges": 4}` is the obvious thing to try, so it is accepted and
    grown into the full shape rather than rejected on a technicality.
    """
    if params in (None, ""):
        return {}
    if not isinstance(params, Mapping):
        raise ForgeError(
            '`params` must be an object of {name: {"value": ...}} (a bare value '
            'is fine too: {"wedges": 4}).'
        )
    out: Dict[str, Any] = {}
    for key, spec in params.items():
        name = str(key).strip()
        if not name:
            raise ForgeError("A flow parameter cannot have an empty name.")
        if isinstance(spec, Mapping):
            if "value" not in spec:
                raise ForgeError(
                    f"Parameter {name!r} has no 'value'. Every parameter declares "
                    "its default, which is also what types it."
                )
            entry: Dict[str, Any] = {
                "value": _json_safe(spec["value"], f"params.{name}.value")
            }
            for field in ("unit", "description"):
                if spec.get(field) not in (None, ""):
                    entry[field] = str(spec[field])
            out[name] = entry
        else:
            out[name] = {"value": _json_safe(spec, f"params.{name}")}
    return out


def normalize_flow_steps(steps: Any) -> List[Dict[str, Any]]:
    """Validate a flow's steps against the known command / endpoint sets."""
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ForgeError(
            "`steps` must be a non-empty list. A flow with no steps does nothing "
            "— and a flow with ONE step is not worth saving either: that is just "
            "the tool call itself."
        )
    out: List[Dict[str, Any]] = []
    for index, raw in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(raw, Mapping):
            raise ForgeError(f"{where} must be an object with kind, op and args.")
        kind = str(raw.get("kind") or "").strip()
        if kind not in ("blender", "service"):
            raise ForgeError(
                f"{where} has kind {raw.get('kind')!r}; it must be \"blender\" (a "
                'Forge socket command) or "service" (a geometry-service endpoint).'
            )
        op = str(raw.get("op") or "").strip()
        if not op:
            raise ForgeError(f"{where} has no 'op'.")
        if kind == "blender":
            if op in ("flow_run", "flow_list"):
                raise ForgeError(
                    f"{where} calls {op!r}. Flows do not nest — write the steps "
                    "out in this flow so what it does is readable in one file."
                )
            if op not in KNOWN_BLENDER_OPS:
                raise ForgeError(
                    f"{where}: {op!r} is not a Blender command. Known commands: "
                    + ", ".join(sorted(KNOWN_BLENDER_OPS))
                )
        else:
            if not op.startswith("/"):
                op = "/" + op
            if op not in KNOWN_SERVICE_OPS:
                raise ForgeError(
                    f"{where}: {op!r} is not a geometry-service endpoint. Known "
                    "endpoints: " + ", ".join(sorted(KNOWN_SERVICE_OPS))
                )
        args = raw.get("args")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ForgeError(f"{where}: 'args' must be an object.")
        step: Dict[str, Any] = {
            "kind": kind,
            "op": op,
            "args": _json_safe(dict(args), f"{where}.args"),
        }
        label = str(raw.get("label") or "").strip()
        if label:
            step["label"] = label
        out.append(step)
    return out


def flow_document(
    name: str,
    slug: str,
    description: Any,
    params: Any,
    steps: Any,
) -> Dict[str, Any]:
    """The JSON a saved flow is, in the field order a human wants to read."""
    text = str(description or "").strip()
    if not text:
        raise ForgeError(
            "A flow needs a `description`: one sentence saying what it does, "
            "because that sentence is the tooltip the artist reads in the panel."
        )
    normalized = normalize_flow_steps(steps)
    if len(normalized) < 2:
        # Saving one call as a flow adds a file, a name and a button for
        # something that was already one call. Refused here rather than left to
        # judgement, so flows/ stays a list of things worth pressing.
        raise ForgeError(
            "A flow with ONE step is not worth saving — that is just the tool "
            f"call itself ({normalized[0]['kind']} {normalized[0]['op']}). Save a "
            "sequence of two or more operations, or nothing."
        )
    return {
        "name": slug,
        "description": text,
        "params": normalize_flow_params(params),
        "steps": normalized,
    }


def flow_step_label(step: Mapping[str, Any], index: int) -> str:
    label = str(step.get("label") or "").strip()
    return label or f"{step.get('kind', '?')} {step.get('op', '?')}"


def read_flow_files() -> Tuple[Path, List[Dict[str, Any]]]:
    """Every flow on disk: `(folder, [{name, description, params, steps, path}])`.

    Read straight from the filesystem rather than through Blender, so `flow_list`
    answers whether or not Blender happens to be running. A file that will not
    parse is listed with its error instead of being hidden.
    """
    root = flows_root()
    if not root.is_dir():
        return root, []
    out: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append({"name": path.stem, "path": str(path), "error": str(exc)})
            continue
        if not isinstance(doc, dict):
            out.append({"name": path.stem, "path": str(path),
                        "error": "the file is not a JSON object"})
            continue
        steps = doc.get("steps") if isinstance(doc.get("steps"), list) else []
        out.append({
            "name": str(doc.get("name") or path.stem),
            "description": str(doc.get("description") or ""),
            "params": doc.get("params") if isinstance(doc.get("params"), Mapping) else {},
            "steps": steps,
            "path": str(path),
        })
    return root, out


def fmt_flow_params(params: Any, indent: str = "      ") -> List[str]:
    if not isinstance(params, Mapping) or not params:
        return [f"{indent}(no parameters)"]
    lines = []
    for name, spec in params.items():
        if isinstance(spec, Mapping):
            value = spec.get("value")
            unit = f" {spec['unit']}" if spec.get("unit") else ""
            description = str(spec.get("description") or "")
        else:
            value, unit, description = spec, "", ""
        shown = json.dumps(value) if isinstance(value, (dict, list)) else value
        line = f"{indent}{str(name):<16.16} = {shown}{unit}"
        if description:
            line = f"{line}   ({description})"
        lines.append(line[:160])
    return lines


def fmt_flow_list(root: Path, flows: Sequence[Mapping[str, Any]]) -> str:
    """The saved flows, in the shape a model should read before improvising."""
    if not flows:
        return (
            f"No saved flows in {root}.\n"
            "  Nothing has been saved yet — do the job with the tools, then "
            "flow_save it so the artist can press a button next time."
        )
    lines = [f"{len(flows)} saved flow(s) in {root}:"]
    for entry in flows:
        name = entry.get("name")
        if entry.get("error"):
            lines.append(f"  {name}  — BROKEN: {entry['error']}")
            continue
        steps = entry.get("steps") or []
        lines.append(f"  {name}  ({len(steps)} step(s))")
        if entry.get("description"):
            lines.append(f"    {entry['description']}")
        lines.extend(fmt_flow_params(entry.get("params")))
        for index, step in enumerate(steps):
            if isinstance(step, Mapping):
                lines.append(
                    f"      {index + 1}. {flow_step_label(step, index)}"
                    f"  [{step.get('kind')} {step.get('op')}]"
                )
    lines.append(
        "  run one with flow_run(name, params) — same result every time, no "
        "model in the loop."
    )
    return "\n".join(lines)


def fmt_flow_run_report(name: str, result: Mapping[str, Any]) -> str:
    """What each step did, in order."""
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    duration = result.get("duration_ms")
    header = f"Ran flow '{result.get('flow') or name}'"
    if isinstance(duration, (int, float)):
        header = f"{header} — {len(steps)} step(s) in {duration / 1000.0:.1f}s"
    lines = [header]
    if result.get("description"):
        lines.append(f"  {result['description']}")
    params = result.get("params")
    if isinstance(params, Mapping) and params:
        lines.append("  parameters used:")
        lines.extend(fmt_flow_params(params, indent="    "))
    lines.append("  steps:")
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        mark = "ok  " if step.get("ok") else "FAIL"
        lines.append(
            f"    {index + 1}. [{mark}] {flow_step_label(step, index)}"
            f"  ({step.get('kind')} {step.get('op')})"
        )
        if step.get("brief"):
            lines.append(f"         {step['brief']}")
    lines.append(
        "  nothing here was decided by a model: a flow replays exactly what was "
        "saved. Say what changed in the scene in plain words."
    )
    return "\n".join(lines)


def fmt_flow_saved(path: Path, doc: Mapping[str, Any], overwritten: bool) -> str:
    """Where the flow landed, and how the artist runs it without asking again."""
    steps = doc.get("steps") or []
    lines = [
        f"{'Replaced' if overwritten else 'Saved'} flow '{doc.get('name')}' — {path}",
        f"  {doc.get('description')}",
        f"  {len(steps)} step(s):",
    ]
    for index, step in enumerate(steps):
        lines.append(
            f"    {index + 1}. {flow_step_label(step, index)}"
            f"  [{step.get('kind')} {step.get('op')}]"
        )
    lines.append("  parameters:")
    lines.extend(fmt_flow_params(doc.get("params"), indent="    "))
    lines.append(
        "  tell the artist: it is now a button — press N, Forge tab, Flows box, "
        f"'{doc.get('name')}' -> Run. The parameters above are editable there."
    )
    return "\n".join(lines)


# --- Phase 7 (meshgen: a picture becomes a mesh) -----------------------------
#
# Three things about this pipeline have to survive into every report, because
# each one is a promise an artist would otherwise be let down by:
#
# * it takes MINUTES (measured: 304 s trellis2 / 249 s pixal3d on the reference
#   12 GB card), so a report that does not say how long it took is hiding the
#   only number they will feel;
# * `progress` is the fraction through the CURRENT STAGE and resets per node, so
#   the stage NAMES are the honest account of the run and the bar is not;
# * the mesh that comes out is diagnosable, not printable. Voxel repair is
#   mandatory, not optional, and the print verdict afterwards is usually a fail
#   on min_wall — that is the correct answer, not a broken tool.

#: What meshgen will take as an input picture — the same five the reference-image
#: path accepts, so a file the artist attached in the panel is one this can use.
MESHGEN_IMAGE_SUFFIXES = REFERENCE_SUFFIXES

#: What the backends write, and what `import_generated` opens.
MESHGEN_OUTPUT_SUFFIXES = (".glb", ".gltf")


def meshgen_image_path(raw: str) -> Path:
    """A picture on disk for /generate3d, or a refusal naming the formats."""
    path = resolve_path(raw, label="image path")
    if path.is_dir():
        raise ForgeError(f"{path} is a folder, not an image file.")
    if path.suffix.lower() not in MESHGEN_IMAGE_SUFFIXES:
        raise ForgeError(
            f"{path.name} is not an image meshgen can read. It takes "
            + ", ".join(MESHGEN_IMAGE_SUFFIXES)
            + " — re-save it as one of those."
        )
    if not path.is_file():
        raise ForgeError(f"No file at {path} (resolved from {raw!r}).")
    return path


def generated_object_name(image: Path) -> str:
    """What to call the object a picture became: the picture's own name.

    A generated ``.glb`` names its mesh whatever the exporter felt like
    (``Mesh_0`` in practice), which tells the artist nothing and collides with
    the next generation. The file they chose is the name they already think in.
    """
    stem = str(getattr(image, "stem", "") or "").strip() or "generated"
    encoded = stem.encode("utf-8")[:_MAX_OBJECT_NAME]
    return encoded.decode("utf-8", errors="ignore") or "generated"


def fmt_duration(seconds: Any) -> str:
    """`304.2` -> `"5 min 04 s"`. Minutes, because that is the unit that stings."""
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if total < 0:
        return "unknown"
    if total < 60:
        return f"{total:.0f} s"
    return f"{int(total) // 60} min {int(total) % 60:02d} s"


def job_seconds(job: Mapping[str, Any]) -> Optional[float]:
    """How long the job has taken, finished or not."""
    ms = job.get("duration_ms")
    if isinstance(ms, (int, float)):
        return float(ms) / 1000.0
    started = job.get("started")
    finished = job.get("finished")
    if isinstance(started, (int, float)) and isinstance(finished, (int, float)):
        return float(finished) - float(started)
    return None


def fmt_vram(vram: Any) -> Optional[str]:
    """The one number that decides whether a setting fits on this card."""
    if not isinstance(vram, Mapping) or not vram:
        return None
    peak = vram.get("peak_gb")
    total = vram.get("total_gb")
    if peak is None:
        return None
    if total is None:
        return f"peak VRAM {fmt_number(peak, 2)} GB"
    return f"peak VRAM {fmt_number(peak, 2)} GB of {fmt_number(total, 2)}"


def fmt_meshgen_stats(stats: Any) -> Optional[str]:
    """`{"verts": ..., "faces": ...}` in whichever spelling the backend used."""
    if not isinstance(stats, Mapping) or not stats:
        return None
    verts = stats.get("verts", stats.get("vertex_count"))
    faces = stats.get("faces", stats.get("face_count"))
    parts = []
    if verts is not None:
        parts.append(f"{verts} verts")
    if faces is not None:
        parts.append(f"{faces} faces")
    for key in ("materials", "textures"):
        if stats.get(key) is not None:
            parts.append(f"{stats[key]} {key}")
    return ", ".join(parts) or None


def fmt_stage_trail(stages: Sequence[str], limit: int = 12) -> Optional[str]:
    """The stages the job actually went through, in order.

    This is what "what took five minutes" looks like when answered honestly.
    """
    names = [str(stage).strip() for stage in stages if str(stage).strip()]
    if not names:
        return None
    if len(names) > limit:
        head = names[: limit - 1]
        return " -> ".join(head) + f" -> ... ({len(names) - limit + 1} more)"
    return " -> ".join(names)


def fmt_job_state(job: Mapping[str, Any]) -> str:
    """State, stage and the per-stage caveat, as one line."""
    state = str(job.get("state") or "?")
    stage = str(job.get("stage") or "").strip()
    progress = job.get("progress")
    bits = [f"state: {state}"]
    if stage:
        bits.append(f"stage: {stage}")
    if isinstance(progress, (int, float)):
        bits.append(f"{float(progress) * 100:.0f}% through THAT stage")
    return "  " + "   ".join(bits)


def check_verdict(check: Optional[Mapping[str, Any]]) -> List[str]:
    """A compact print verdict for a generated mesh: row statuses, no detail.

    ``check_model`` renders the full report; this is the two-line version that
    belongs at the end of a generation, and it is deliberately blunt about what
    a generated mesh usually scores.
    """
    if not isinstance(check, Mapping) or not check:
        return []
    overall = str(check.get("overall", "?")).upper()
    rows = [row for row in (check.get("checks") or []) if isinstance(row, Mapping)]
    marks = "  ".join(
        f"[{str(row.get('status', '?')).upper()}] {row.get('name', '?')}" for row in rows
    )
    lines = [f"  print verdict: {overall}"]
    if marks:
        lines.append(f"    {marks}")
    failed = [str(row.get("name")) for row in rows if row.get("status") == "fail"]
    if failed:
        lines.append(
            "    "
            + ", ".join(failed)
            + " failed — usual for a generated mesh, and a real answer: it says "
            "what to fix before printing, not that the model is wrong."
        )
    return lines


#: Said at the end of every generation, because every one of these is a thing an
#: artist would otherwise discover on a failed print or a ruined rig.
GENERATED_MESH_ADVICE = (
    "  What this mesh is: a sculpt-like starting shape, not a precise part. It "
    "has no crisp flat faces, no exact dimensions and no fine detail — never "
    "promise those. Scale and wall thickness are DECISIONS the artist still has "
    "to make before printing (nothing in the picture said how big it is).\n"
    "  Next steps to offer: for a game asset, rigforge_retopo (clean quads, then "
    "tags/UV/rig); for printing, check_model and then fix what it names; for "
    "looks, sculpting is theirs — offer the polish walkthrough rather than "
    "pretending a tool does it."
)


def fmt_generate_report(
    image: Path,
    submitted: Mapping[str, Any],
    job: Mapping[str, Any],
    stages: Sequence[str],
    imported: Optional[Mapping[str, Any]] = None,
    check: Optional[Mapping[str, Any]] = None,
    problems: Optional[Sequence[str]] = None,
) -> str:
    """Picture in, object in the scene, verdict — the whole run in one report."""
    state = str(job.get("state") or submitted.get("state") or "?").lower()
    mesh_path = job.get("mesh_path") or job.get("output") or submitted.get("output")
    backend = job.get("backend") or submitted.get("backend") or "?"
    model = job.get("model")
    seconds = job_seconds(job)

    if state == "done" and imported:
        headline = (
            f"Generated a 3D shape from {image.name} and imported it as "
            f"'{imported.get('object')}'."
        )
    elif state == "done":
        headline = f"Generated a 3D shape from {image.name}."
    else:
        headline = f"Generation from {image.name} did not finish (state: {state})."

    lines = [headline, f"  backend: {backend}" + (f" ({model})" if model else "")]
    if seconds is not None:
        lines.append(f"  took {fmt_duration(seconds)}")
    trail = fmt_stage_trail(stages)
    if trail:
        lines.append(f"  stages: {trail}")
        lines.append("    (meshgen reports progress per stage, never for the whole job)")
    stats = fmt_meshgen_stats(job.get("stats"))
    if stats:
        lines.append(f"  raw output: {stats}")
    vram = fmt_vram(job.get("vram"))
    if vram:
        lines.append(f"  {vram}")
    if mesh_path:
        lines.append(f"  file: {mesh_path}")
    if job.get("error"):
        lines.append(f"  error: {job['error']}")

    if imported:
        counts = f"{imported.get('vertex_count')} verts, {imported.get('face_count')} faces"
        if imported.get("repaired"):
            voxel = imported.get("voxel_size_mm")
            before = imported.get("before") or {}
            lines.append(
                f"  in Blender: '{imported.get('object')}' — {counts}, voxel-repaired"
                + (f" at {fmt_number(voxel, 3)} mm" if voxel is not None else "")
            )
            if before.get("face_count"):
                lines.append(
                    f"    (the raw mesh had {before.get('face_count')} faces and was "
                    "not manifold — the repair is mandatory, not a preference)"
                )
        else:
            lines.append(
                f"  in Blender: '{imported.get('object')}' — {counts}, NOT repaired"
            )
        dims = imported.get("dimensions_mm")
        if isinstance(dims, (list, tuple)) and len(dims) == 3:
            lines.append(
                f"    size as imported: {_dims(dims)} — the picture never said how "
                "big it is, so scale it to whatever the artist tells you"
            )

    lines.extend(check_verdict(check))

    for problem in problems or []:
        lines.append(f"  NOTE: {problem}")

    if state == "done" and not imported:
        lines.append(
            "  The file above is on disk and safe. Nothing is in the scene: import "
            "it with import_generated once Blender is running (path above)."
        )
    if state in ("queued", "running"):
        job_id = job.get("job_id") or submitted.get("job_id")
        lines.append(
            f'  Still running. Follow it with meshgen_status(job_id="{job_id}") — '
            "nothing was lost, and cancelling is a separate decision."
        )

    if state == "done":
        lines.append("")
        lines.append(GENERATED_MESH_ADVICE)
    return "\n".join(lines)


def fmt_submitted_report(image: Path, submitted: Mapping[str, Any]) -> str:
    """wait=false: the job id and how to follow it, with nothing pretended."""
    job_id = submitted.get("job_id")
    return "\n".join([
        f"Started a 3D generation from {image.name}.",
        f"  job id: {job_id}",
        f"  backend: {submitted.get('backend')}   will write: {submitted.get('output')}",
        f'  Poll it with meshgen_status(job_id="{job_id}"). It takes about five '
        "minutes on this machine — say so before the artist starts waiting.",
        "  When it says done, import it with import_generated(path=<the file above>) "
        "— it arrives voxel-repaired, and check_model gives the print verdict.",
    ])


def fmt_missing_models(payload: Mapping[str, Any]) -> List[str]:
    """The "what to download and where it goes" lines, straight from /health."""
    missing = [entry for entry in (payload.get("missing") or []) if isinstance(entry, Mapping)]
    if not missing:
        return []
    lines = [f"  {len(missing)} model file(s) missing — nothing downloads on its own:"]
    for entry in missing[:8]:
        lines.append(f"    {entry.get('what')}  ->  {entry.get('path')}")
        if entry.get("source"):
            lines.append(f"      from {entry.get('source')}")
    if payload.get("hint"):
        lines.append(f"  {payload['hint']}")
    return lines


def fmt_meshgen_status(
    payload: Optional[Mapping[str, Any]],
    job: Optional[Mapping[str, Any]] = None,
    detail: str = "",
) -> str:
    """/health (+ one job) in words an artist can act on."""
    if not payload:
        lines = [f"Picture-to-3D (meshgen) [DOWN] {config.meshgen_address()}"]
        if detail:
            lines.append(f"  {detail}")
        lines.append(
            "  This one is optional: it needs an 18.5 GB model download "
            "(meshgen/README.md). Everything else in Forge works without it, and "
            "a shape that needs real dimensions should be parametric anyway."
        )
        return "\n".join(lines)

    status = str(payload.get("status") or "?")
    backend = payload.get("backend") if isinstance(payload.get("backend"), Mapping) else {}
    lines = [
        f"Picture-to-3D (meshgen) [UP] {config.meshgen_address()} — status: {status}"
    ]
    if backend:
        lines.append(
            f"  backend: {backend.get('name')} ({backend.get('model')}), "
            f"licence {backend.get('license')}, about {backend.get('vram_gb')} GB VRAM"
        )
    others = [
        str(entry.get("name"))
        for entry in (payload.get("available_backends") or [])
        if isinstance(entry, Mapping) and entry.get("name") != (backend or {}).get("name")
    ]
    if others:
        lines.append(f"  also installed: {', '.join(others)}")
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), Mapping) else {}
    if jobs:
        lines.append(
            f"  jobs: {jobs.get('active', 0)} active, {jobs.get('queued', 0)} queued "
            "(one at a time — a 12 GB card cannot hold two of these models)"
        )
    if payload.get("comfyui_running") is not None:
        lines.append(
            "  model host: "
            + ("running" if payload.get("comfyui_running")
               else "not started yet (it starts on the first job, costing ~10 s)")
        )
    lines.extend(fmt_missing_models(payload))

    if job:
        lines.append("")
        lines.append(f"Job {job.get('job_id')}")
        lines.append(fmt_job_state(job))
        seconds = job_seconds(job)
        if seconds is not None:
            lines.append(f"  {fmt_duration(seconds)} so far")
        if job.get("image_path"):
            lines.append(f"  from: {job.get('image_path')}")
        if job.get("mesh_path"):
            lines.append(f"  wrote: {job.get('mesh_path')}")
        stats = fmt_meshgen_stats(job.get("stats"))
        if stats:
            lines.append(f"  {stats}")
        vram = fmt_vram(job.get("vram"))
        if vram:
            lines.append(f"  {vram}")
        if job.get("error"):
            lines.append(f"  error: {job.get('error')}")
        state = str(job.get("state") or "").lower()
        if state in ("queued", "running"):
            lines.append(
                "  Nothing to do but wait — a full run is about five minutes on "
                "this machine. Tell the artist which stage it is on, not a percentage."
            )
        elif state == "done":
            lines.append(
                "  Done: import it with import_generated(path=<the file above>) — it "
                "arrives voxel-repaired, then check_model gives the print verdict."
            )
    return "\n".join(lines)
