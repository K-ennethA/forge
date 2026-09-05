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
    *, has_tags: bool, has_retopo: bool, has_metarig: bool, has_rig: bool
) -> str:
    """The one next call, walking the pipeline backwards from the far end."""
    if has_rig:
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
