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
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree

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

#: A bed_fit failure the printer can simply cut up is not a failure at all.
_SPLIT_TAG = "[SPLIT]"

#: Worst-first, for the design-time verdict.
_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}


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


def feasible_split(check: Any) -> Optional[Mapping[str, Any]]:
    """The print-time split behind a bed_fit failure, or None.

    THE DOCTRINE: a part is designed at the size it SHOULD be. Bed fit is
    print planning, not a design constraint. A `bed_fit` fail that comes with a
    feasible `suggested_segmentation` is INFORMATIONAL — "this prints as N
    pieces" — never a fault to fix and never a reason to shrink the design. The
    wire `overall` stays exactly as the service sent it (compatibility); only
    the rendering is reframed. It is a genuine problem ONLY when the suggestion
    is infeasible — the footprint is too big even segmented — and then the
    options are scaling the part down or redesigning it, with the user.
    """
    if not isinstance(check, Mapping):
        return None
    if str(check.get("name")) != "bed_fit":
        return None
    if str(check.get("status", "")).lower() != "fail":
        return None
    data = check.get("data") if isinstance(check.get("data"), Mapping) else {}
    suggestion = data.get("suggested_segmentation")
    if not isinstance(suggestion, Mapping):
        return None
    if not suggestion.get("feasible") or suggestion.get("mode") is None:
        return None
    return suggestion


def split_phrase(suggestion: Mapping[str, Any]) -> str:
    """`prints as 4 radial pieces` — the human half of a feasible split."""
    mode = suggestion.get("mode")
    kind = str(suggestion.get("kind") or "").strip()
    count: Optional[int] = None
    if isinstance(mode, Mapping):
        radial = mode.get("radial")
        planar = mode.get("planar")
        if isinstance(radial, (int, float)) and not isinstance(radial, bool):
            count = int(radial)
            kind = kind or "radial"
        elif isinstance(planar, (list, tuple)):
            count = len(planar) + 1
            kind = kind or "planar"
    if count is None or count < 2:
        return "prints in more than one piece" + (f" ({kind})" if kind else "")
    return f"prints as {count} {kind} pieces".replace("  ", " ")


def _split_note(checks: Any) -> Optional[Mapping[str, Any]]:
    """The first feasible split among a check list, or None."""
    for check in checks or []:
        suggestion = feasible_split(check)
        if suggestion is not None:
            return suggestion
    return None


def _design_verdict(payload: Mapping[str, Any]) -> str:
    """The verdict as a human should read it: a feasible split is not a fail.

    The service's own `overall` is untouched on the wire. This is presentation
    only — the worst status among the checks that actually constrain the
    DESIGN, which a bed_fit failure with a printable split does not.
    """
    checks = payload.get("checks") or []
    worst = -1
    seen = False
    for check in checks:
        if not isinstance(check, Mapping):
            continue
        seen = True
        status = "pass" if feasible_split(check) else str(check.get("status", "")).lower()
        worst = max(worst, _STATUS_RANK.get(status, 2))
    if not seen:
        return str(payload.get("overall", "?")).upper()
    for name, rank in _STATUS_RANK.items():
        if rank == worst:
            return name.upper()
    return str(payload.get("overall", "?")).upper()


def _readiness_lines(subject: str, payload: Mapping[str, Any]) -> List[str]:
    """The headline, reframed when the part is simply bigger than the bed."""
    overall = str(payload.get("overall", "?")).upper()
    split = _split_note(payload.get("checks"))
    if split is None:
        return [f"Print readiness: {overall} — {subject}"]
    verdict = _design_verdict(payload)
    prefix = "passes everything that matters at design time; " if verdict == "PASS" else ""
    return [
        f"Print readiness: {verdict} at design time — {subject}",
        f"  {prefix}{split_phrase(split)} at print time — bed size is not a design "
        f"constraint, so it does not count against the verdict (service overall: "
        f"{overall}).",
    ]


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
        lines.append(f"{indent}not a design problem: design at the true size and let "
                     f"the print-time split handle it. Never shrink a part to fit "
                     f"the bed.")
    else:
        lines.append(f"{indent}NOT segmentable automatically — cutting cannot fix this, "
                     f"so this one IS a real problem: scale the part down or redesign "
                     f"it, and talk it over with the user first.")
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

        # Bigger than the bed, but it cuts up cleanly: that is a print-time
        # fact, not a design failure, so it does not wear the failure styling.
        split = feasible_split(check)
        if split is not None:
            tag = _SPLIT_TAG
            summary = f"{split_phrase(split)} (handled at print time) — {summary}"
        else:
            tag = _tag(check.get("status"))
        lines.append(f"  {tag} {name:<11} {summary}")

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
    lines = [
        *_readiness_lines(script_name, payload),
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
    lines = [
        *_readiness_lines(f"{mesh_subject(object_name, result)} (imported mesh)", result),
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
    lines.extend(fmt_joint_hints(result.get("joints")))
    lines.append(
        "  next: check the placement in Blender, then rigforge_generate_rig."
    )
    return "\n".join(lines)


def fmt_joint_hints(joints: Any) -> List[str]:
    """The `joints_file` block, summarised — never the raw per-role dump.

    Predictions are HINTS: a detector scores F1 ~0.077 on out-of-domain
    skeletons and, measured here, put a shoulder 253 mm out. So what this prints
    is the three numbers that decide whether to trust the fit — how many
    landmarks moved, how far the largest move was, and how many disagreements
    the tags overruled — and never a wall of coordinates.
    """
    if not isinstance(joints, Mapping) or not joints:
        return []
    if not joints.get("enabled"):
        return [
            "  predicted joints: read and NOT used — the tag-only fit is what "
            "you are looking at. The warnings above say why."
        ]
    refined = joints.get("refined") or []
    disagreements = joints.get("disagreements") or []
    best_effort = joints.get("best_effort") or []
    moves = [float(entry.get("moved_mm") or 0.0) for entry in refined
             if isinstance(entry, Mapping)]
    out = [
        f"  predicted joints: {fmt_number(len(refined), 0)} landmark(s) refined"
        + (f", largest move {fmt_number(max(moves), 1)} mm" if moves else "")
        + f", {fmt_number(len(disagreements), 0)} disagreement(s) overruled"
    ]
    if disagreements:
        out.append(
            "    the TAGS won every one of those — say so if you quote the fit. "
            "A second opinion was recorded, not obeyed."
        )
    if best_effort:
        out.append(
            f"    {fmt_number(len(best_effort), 0)} role(s) placed from "
            "predictions alone (past the wrist, where no tag can reach) — "
            "best effort, not fitted"
        )
    return out


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


#: Blender truncates object names at 63 bytes, so the add-on's
#: ``common.component_name`` caps there — and a spec that recorded a name
#: Blender cannot give an object would be recording a lie.
COMPONENT_NAME_LIMIT = 63


def component_name(project: str, component: Any = None) -> str:
    """``<project>`` for the core, ``<project>-<component>`` for a proposal.

    The mirror of the add-on's ``forge.tools.common.component_name``: the two
    halves of Forge have to spell a component the same way or "scrap the collar"
    finds nothing.
    """
    stem = str(project or "").strip()
    piece = str(component or "").strip().strip("-")
    if piece.startswith(stem + "-") and stem:
        name = piece                      # already a full name; do not double it
    else:
        name = f"{stem}-{piece}" if piece else stem
    return name.encode("utf-8")[:COMPONENT_NAME_LIMIT].decode("utf-8", "ignore")


def component_block(slug: str, components: Any) -> Optional[Dict[str, Any]]:
    """The spec's record of a component tree, or ``None`` when there is none.

    ``["collar", "ear-l"]`` and ``["gecko-bowl-collar", "gecko-bowl-ear-l"]``
    both record the same two proposals, because the assistant will reach for
    either and the naming rule is the same one either way. The core and the
    collection are the project itself, which is the whole convention.
    """
    if components in (None, "", [], ()):
        return None
    if isinstance(components, str) or not isinstance(components, (list, tuple)):
        raise ForgeError(
            "`components` is the list of proposal pieces a design lands as — "
            '["collar", "ear-l", "tail"]. The core is the part itself and does '
            "not go in the list."
        )
    proposals: List[str] = []
    for entry in components:
        if not isinstance(entry, str) or not entry.strip():
            raise ForgeError("Every component is a name, e.g. \"collar\".")
        name = component_name(slug, entry)
        if name == slug:
            continue                      # that is the core, not a proposal
        if name not in proposals:
            proposals.append(name)
    return {"collection": slug, "core": slug, "proposals": proposals}


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
    components: Any = None,
) -> Dict[str, Any]:
    """A minimal spec.json for a new part, shaped like templates/spec.json.

    The parameters are mirrored from the schema the service just resolved, so
    the spec and the script cannot disagree on the day they are written.
    ``components`` records the component tree (Phase 11) when the design has
    one, so a later session knows which object is the core and which are
    proposals that can be scrapped.
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

    block = component_block(slug, components)
    document: Dict[str, Any] = {
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
    }
    # Right after "features", where a reader asking "what pieces is this?" is
    # already looking, and only when there is a tree to record.
    if block is not None:
        document["components"] = block
    document.update({
        "print": {
            "printer": config.SPEC_PRINTER_REF,
            "segments": "auto",
            "joint_type": "dovetail",
            "mold_mode": False,
        },
        "script": "part.py",
        "exports": [],
    })
    return document


def update_spec_components(spec: Path, slug: str, components: Any) -> str:
    """Record the component tree in an existing spec.json. ``""`` when it worked.

    Revising a design is the common case — the collar was wrong, the tail is
    new — and the spec is the artist's file with their own edits in it. So this
    rewrites exactly one key and merges the proposals it is given with the ones
    already recorded, and any spec it cannot read is left untouched with a
    sentence handed back rather than replaced by a fresh one.
    """
    block = component_block(slug, components)
    if block is None:
        return ""
    try:
        document = json.loads(spec.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"spec.json was left alone — it could not be read: {exc}"
    if not isinstance(document, dict):
        return "spec.json was left alone — it does not contain a JSON object."

    existing = document.get("components")
    if isinstance(existing, Mapping):
        known = [name for name in (existing.get("proposals") or [])
                 if isinstance(name, str)]
        merged = list(known)
        for name in block["proposals"]:
            if name not in merged:
                merged.append(name)
        block = dict(existing)
        block.update({"collection": document.get("name") or slug,
                      "core": document.get("name") or slug,
                      "proposals": merged})

    document["components"] = block
    try:
        spec.write_text(json.dumps(document, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    except OSError as exc:
        return f"spec.json could not be updated: {exc}"
    return ""


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


# --- Phase 16 (the design phase: requirements before geometry) ---------------
#
# The design phase writes prose and pictures BEFORE any geometry exists, so
# `projects/<slug>/design/` is routinely the first thing in a project folder —
# there is no part.py yet, and there may never be one if the sheet gets torn up
# at the sign-off gate. That is the whole point of the phase, and it is why this
# writer does not require the project to exist first.
#
# It is also the third and last tool in this server that writes to disk, so it
# obeys `partforge_new_part`'s rules exactly: the project name is slugged, a
# filename is a filename or it is refused, and the resolved path is checked a
# second time against the folder it must be under.

#: The one folder under a project that design documents may be written to.
DESIGN_DIRNAME = "design"

#: What a design document may be. Prose, a hand-authored schematic, structured
#: numbers — and nothing that runs: `partforge_new_part` is the only tool that
#: writes source, and this must not become a second one.
DESIGN_EXTENSIONS = (".md", ".svg", ".json")

#: Long enough for "requirements-revision-2.md", short enough to stay readable.
DESIGN_NAME_MAX = 80

#: A design filename is ONE plain name. Deliberately the same alphabet the
#: bridge allows for its own assets: a percent sign is not in it, so an encoded
#: traversal fails on the alphabet rather than on path arithmetic.
_DESIGN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The order a design sheet reads in, which is the order it is written in.
#: Anything else follows alphabetically. `mechanism.svg` (Phase 17) is the
#: concept sketch with the motion in it — the press stroke, the latch, the LED —
#: so it reads directly after `concept.svg` and before the parts list.
#: `floorplan.svg` (Phase 19) is the same document one scale up: the assistant's
#: READING of a drawing, echoed back for approval before any wall is built. It
#: is a picture and it is the thing being signed off, so it sits with the other
#: diagrams rather than filed alphabetically. `floorplan.json` is deliberately
#: NOT named here — it is the machine's copy of what the picture says, and it
#: follows alphabetically like any other structured file.
DESIGN_READING_ORDER = ("requirements.md", "concept.svg", "mechanism.svg",
                        "floorplan.svg", "components.md")


def design_filename(filename: Any) -> str:
    """`"requirements.md"` -> itself, or a refusal that says why.

    Not slugged. A slug would have to invent an extension, and an extension is
    the one thing here that carries meaning — `.svg` is a picture the artist
    will look at, `.md` is a sheet they will read. So a name that is not already
    a plain filename with a known extension is refused rather than cleaned.
    """
    raw = "" if filename is None else str(filename).strip().strip('"').strip()
    if not raw:
        raise ForgeError(
            "No filename given. A design document is one plain name — "
            '"requirements.md", "concept.svg", "components.md".'
        )
    for marker in _TRAVERSAL_MARKERS:
        if marker in raw:
            raise ForgeError(
                f"{filename!r} is not a filename — it looks like a path (it "
                f"contains {marker!r}). Design documents are always written to "
                'projects/<project>/design/, so pass just the name, e.g. '
                '"requirements.md".'
            )
    if raw.startswith("~") or raw.startswith("%") or raw.startswith("$"):
        raise ForgeError(
            f"{filename!r} is not a filename — it looks like a path or an "
            'environment variable. Pass just the name, e.g. "requirements.md".'
        )
    if not _DESIGN_NAME_RE.match(raw):
        raise ForgeError(
            f"{filename!r} is not a usable filename. Letters, digits, dots, "
            "dashes and underscores only, starting with a letter or a digit — "
            'e.g. "requirements.md" or "concept-front.svg".'
        )
    if len(raw) > DESIGN_NAME_MAX:
        raise ForgeError(
            f"{filename!r} is longer than {DESIGN_NAME_MAX} characters. Give the "
            'document a short name — "requirements.md".'
        )
    suffix = Path(raw).suffix.lower()
    if suffix not in DESIGN_EXTENSIONS:
        raise ForgeError(
            f"{raw} is not a design document. Design documents are "
            + ", ".join(DESIGN_EXTENSIONS)
            + " — the sheet (.md), the concept diagram (.svg), or structured "
            "numbers (.json). A part script is not one of these: that is "
            "partforge_new_part's job, and it comes after the sign-off gate."
        )
    return raw


def design_root(slug: str) -> Path:
    """``projects/<slug>/design/`` — the one folder design documents live in."""
    folder, _script, _spec = project_paths(slug)
    return (folder / DESIGN_DIRNAME).resolve()


def design_paths(slug: str, filename: str) -> Tuple[Path, Path]:
    """``(design folder, file)`` for a slug, checked to stay in projects/.

    Two independent guards, the same pair :func:`project_paths` uses: the
    filename rules above, and then the resolved path measured against the folder
    it has to be inside.
    """
    design = design_root(slug)
    path = (design / filename).resolve()
    try:
        path.relative_to(design)
    except ValueError:
        raise ForgeError(
            f"{filename!r} would write outside {design}. Design documents only "
            "ever land in projects/<project>/design/."
        ) from None
    return design, path


def normalize_design_content(content: Any, filename: str) -> str:
    """The document as it will be written: LF endings, one trailing newline.

    A `.svg` is parsed as XML first and a `.json` is parsed as JSON first — both
    are self-authored, and that is exactly why they are checked. A diagram that
    was cut off mid-tag renders as nothing at all in the artist's chat, and a
    blank rectangle where the concept sketch should be is worse than no sketch:
    it looks like the tool is broken rather than like the file is.
    """
    if content is None or not isinstance(content, str) or not content.strip():
        raise ForgeError(
            f"No content given for {filename}. Pass the whole document — the "
            "requirements sheet, or the SVG source of the diagram."
        )
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    suffix = Path(filename).suffix.lower()
    if suffix == ".svg":
        check_svg(text, filename)
    elif suffix == ".json":
        try:
            json.loads(text)
        except ValueError as exc:
            raise ForgeError(
                f"{filename} is not valid JSON ({exc}). Nothing was written — "
                "a half-written file is worse than none."
            ) from None
    return text if text.endswith("\n") else text + "\n"


def check_svg(text: str, filename: str) -> None:
    """Refuse anything that is not a parseable SVG document. Returns nothing."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ForgeError(
            f"{filename} is not valid XML ({exc}), so it would not draw. "
            "Nothing was written. Check every tag is closed — an SVG that was "
            "cut off mid-element renders as an empty box in the artist's chat, "
            "which reads as a broken tool rather than a broken file."
        ) from None
    tag = root.tag
    # ElementTree keeps the namespace on the tag: '{http://www.w3.org/2000/svg}svg'.
    local = tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
    if local.lower() != "svg":
        raise ForgeError(
            f"{filename} parses as XML but its root element is <{local or tag}>, "
            "not <svg>, so nothing would draw. Nothing was written. A concept "
            'diagram starts with <svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 800 500">.'
        )


def design_documents(slug: str) -> List[Dict[str, Any]]:
    """Every document in a project's design folder, in reading order."""
    design = design_root(slug)
    try:
        names = sorted(entry.name for entry in design.iterdir()
                       if entry.is_file())
    except OSError:
        return []
    ranked = sorted(
        names,
        key=lambda name: (DESIGN_READING_ORDER.index(name.lower())
                          if name.lower() in DESIGN_READING_ORDER
                          else len(DESIGN_READING_ORDER), name.lower()),
    )
    out: List[Dict[str, Any]] = []
    for name in ranked:
        path = design / name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        out.append({"file": name, "path": str(path), "size": int(size)})
    return out


def fmt_design_saved(
    *,
    slug: str,
    path: Path,
    documents: List[Dict[str, Any]],
    overwritten: bool,
) -> str:
    """Where the document landed, and where the artist will find it."""
    suffix = path.suffix.lower()
    lines = [
        f"{'Updated' if overwritten else 'Saved'} {path.name} — {path}",
        f"  design sheet for {slug}: "
        + ", ".join(item["file"] for item in documents),
    ]
    if suffix == ".svg":
        lines.append(
            "  it is a picture: name that full path in your reply and it renders "
            "inline in the artist's chat, so they see the diagram rather than a "
            "description of it."
        )
    lines.append(
        f"  the {slug} card in the Library lists the design sheet, so this "
        "survives the conversation."
    )
    lines.append(
        "  nothing has been built. The design phase ends at the sign-off gate: "
        "show the sheet, then ask them to say build it — or to say what to "
        "change on it."
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

#: The counter alone is NOT unique: it restarts at 1 in every new server process,
#: and the assistant bridge starts a fresh one per conversation. The dogfood run
#: of 2026-09-16 (finding B-4) caught what that costs — four turns wrote
#: `preview-001-iso.png`, every one of them minted the same file token, and
#: scrolling back through the chat showed the LAST render in all four messages.
#: This stamp makes the name unique per process as well as per render, so a
#: picture the artist was shown stays the picture they were shown.
_PREVIEW_RUN_TAG = f"{os.getpid():x}{int(time.time() * 1000) & 0xFFFFFF:06x}"

#: Previews are scratch by design (config.PREVIEWS_DIR is under %TEMP%), and
#: now that every render mints its own file the folder only grows. That is the
#: intended trade — a stale picture in the artist's chat is worse than a few MB
#: of PNGs in a temp folder the OS clears — and it is documented in
#: mcp/README.md beside FORGE_PREVIEWS_DIR rather than swept up automatically:
#: deleting a render a chat message still points at would turn B-4's wrong
#: picture into no picture at all.
PREVIEWS_ARE_SCRATCH = (
    "Previews are never deleted by Forge: each render mints its own file so an "
    "older chat message keeps its own picture. The folder is scratch — "
    "FORGE_PREVIEWS_DIR, %TEMP%\\forge-previews by default — and is safe to "
    "empty whenever nothing on screen needs its pictures."
)


def preview_path(view: str, objects: Sequence[str] | None = None) -> Path:
    """A fresh scratch .png to render into, its folder already made.

    The name is unique for the life of the machine, not just of this process:
    counter for reading order, run tag so two servers never collide.
    """
    global _preview_counter

    _preview_counter += 1
    stem = f"preview-{_preview_counter:03d}-{view}"
    names = [str(n).strip() for n in (objects or []) if str(n).strip()]
    if len(names) == 1:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", names[0]).strip("-")[:40]
        if slug:
            stem = f"{stem}-{slug}"
    path = Path(config.PREVIEWS_DIR) / f"{stem}-{_PREVIEW_RUN_TAG}.png"
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


# --- The geometric gate (verify_design / turntable) --------------------------
#
# The second half of a two-gate loop. `render_preview` and `capture_viewport`
# judge beauty; these judge truth. The reason they are separate tools rather
# than one is measured rather than felt: render-based judging systematically
# rewards visual impact over downstream utility (the same model scores 78 ELO
# higher shown as a splat than as a mesh), and ~26% of paired visual judgements
# reverse when the two candidates swap places. A loop that only looks is a loop
# that can be fooled.

#: What `for` may be, and what each profile gates.
VERIFY_PROFILES = ("game", "print", "any")

#: The judging rig that survived the protocol research: 24 views at 256 px.
#: Exposed here so the MCP refusal quotes the same numbers the add-on does.
TURNTABLE_VIEWS = 24
TURNTABLE_MIN_VIEWS = 4
TURNTABLE_MAX_VIEWS = 64
TURNTABLE_RESOLUTION = 256
TURNTABLE_MIN_RESOLUTION = 64
TURNTABLE_MAX_RESOLUTION = 512

#: Bumped per sheet, like the preview counter: comparing this turntable against
#: the last one needs both files to still exist.
_turntable_counter = 0


def turntable_path(views: int, objects: Sequence[str] | None = None) -> Path:
    """A fresh scratch .png for one contact sheet, its folder already made."""
    global _turntable_counter

    _turntable_counter += 1
    stem = f"turntable-{_turntable_counter:03d}-{int(views)}v"
    names = [str(n).strip() for n in (objects or []) if str(n).strip()]
    if len(names) == 1:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", names[0]).strip("-")[:40]
        if slug:
            stem = f"{stem}-{slug}"
    path = Path(config.PREVIEWS_DIR) / f"{stem}.png"
    ensure_parent_dir(path)
    return path


def normalize_turntable_views(views: Any) -> int:
    """How many views, or a refusal that names the range and the default."""
    if views is None:
        return TURNTABLE_VIEWS
    if isinstance(views, bool) or not isinstance(views, (int, float)):
        raise ForgeError(f"views must be a whole number (got {views!r}).")
    if isinstance(views, float) and not float(views).is_integer():
        raise ForgeError(f"views must be a whole number (got {views}).")
    value = int(views)
    if not TURNTABLE_MIN_VIEWS <= value <= TURNTABLE_MAX_VIEWS:
        raise ForgeError(
            f"views must be between {TURNTABLE_MIN_VIEWS} and "
            f"{TURNTABLE_MAX_VIEWS} (got {value}). {TURNTABLE_VIEWS} is the "
            "default because it is the turntable that was actually measured to "
            "work; fewer hides the sides between the ones you kept."
        )
    return value


def normalize_turntable_resolution(resolution: Any) -> int:
    """Pixels per tile, or a refusal naming the range."""
    if resolution is None:
        return TURNTABLE_RESOLUTION
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)):
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution!r})."
        )
    if isinstance(resolution, float) and not float(resolution).is_integer():
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution})."
        )
    value = int(resolution)
    if not TURNTABLE_MIN_RESOLUTION <= value <= TURNTABLE_MAX_RESOLUTION:
        raise ForgeError(
            f"resolution must be between {TURNTABLE_MIN_RESOLUTION} and "
            f"{TURNTABLE_MAX_RESOLUTION} pixels per tile (got {value}). "
            f"{TURNTABLE_RESOLUTION} is the default: it is what the judging "
            "protocol uses, and a bigger tile buys nothing when there are 24 of "
            "them on one sheet."
        )
    return value


def normalize_poly_budget(budget: Any) -> int | None:
    """A face-count target, or a refusal. ``None`` means 'use the profile's'."""
    if budget is None:
        return None
    if isinstance(budget, bool) or not isinstance(budget, (int, float)):
        raise ForgeError(f"poly_budget must be a whole number of faces (got {budget!r}).")
    if isinstance(budget, float) and not float(budget).is_integer():
        raise ForgeError(f"poly_budget must be a whole number of faces (got {budget}).")
    value = int(budget)
    if value < 0:
        raise ForgeError(
            f"poly_budget cannot be negative (got {value}). Pass 0 to measure "
            "the face count without gating it."
        )
    return value


#: How an axis's status reads in the report. The words are chosen so a skim
#: answers the only question that matters — is this blocking "done" or not.
_AXIS_LABEL = {
    "pass": "PASS      ",
    "attention": "ATTENTION ",
    "reported": "REPORT    ",
    "not_applicable": "n/a       ",
}

_AXIS_TITLE = {
    "defects": "defects",
    "poly_budget": "poly budget",
    "uv": "UVs",
    "loops": "edge loops at joints",
    "symmetry": "symmetry residual",
    "silhouette": "silhouette vs reference",
}


def _tier_of(entry: Mapping[str, Any]) -> str:
    tier = str(entry.get("tier") or "")
    return tier or "measured"


def fmt_verify_report(result: Mapping[str, Any]) -> str:
    """`verify_design` as a scored gate an artist and a model can both read.

    Three things this report has to do and a plain dict dump would not:

    1. **Say pass or attention per axis**, so "is this done" is answerable by
       skimming a column rather than by reading numbers and forming an opinion.
    2. **Stamp every claim with its credibility tier.** A silhouette IoU
       thresholded off somebody's photograph and a face count are both numbers
       and they are not both facts, and a report that presented them alike would
       teach the reader to trust the wrong one.
    3. **Say the other half of the gate out loud.** This is the truth gate. It
       does not know whether the thing is beautiful and it must never be quoted
       as though it did.
    """
    name = result.get("object") or "the mesh"
    profile = str(result.get("for") or "any")
    axes: Mapping[str, Any] = result.get("axes") or {}
    gated = [str(a) for a in (result.get("gated_axes") or [])]
    attention = [str(a) for a in (result.get("attention") or [])]
    passed = [str(a) for a in (result.get("passed") or [])]
    gate = str(result.get("gate") or "pass")

    headline = (
        f"{name}: geometric gate {'NEEDS ATTENTION' if gate == 'attention' else 'PASSES'}"
        f" — {len(passed)} of {len(gated)} gated axes clean"
        f" (profile: {profile}, {fmt_number(result.get('face_count'), 0)} faces)."
    )
    lines = [headline, ""]

    # The axes, gated ones first: what is blocking "done" belongs at the top.
    order = gated + [a for a in axes if a not in gated]
    for key in order:
        entry = axes.get(key)
        if not isinstance(entry, Mapping):
            continue
        label = _AXIS_LABEL.get(str(entry.get("status")), "          ")
        title = _AXIS_TITLE.get(key, key)
        tier = _tier_of(entry)
        gate_mark = "" if key in gated else "  (not gated for this profile)"
        lines.append(f"  [{label.strip()}] {title} ({tier}){gate_mark}")
        lines.append(f"      {entry.get('summary')}")

    verdict = [str(v) for v in (result.get("verdict") or [])]
    if verdict:
        lines.append("")
        lines.append("  Worst first, each stamped with how much it can be trusted:")
        for sentence in verdict:
            lines.append(f"    {sentence}")

    detail = _verify_detail_lines(axes)
    if detail:
        lines.append("")
        for entry in detail:
            lines.append(f"    {entry}")

    notes = [str(n) for n in (result.get("notes") or [])]
    if notes:
        lines.append("")
        for note in notes:
            lines.append(f"  note: {note}")

    lines.append("")
    lines.append(
        "  measured = a computed number with a definition behind it. "
        "heuristic = a number that took a judgement call to compute — real "
        "information, wrong to quote as fact. Say which you are quoting."
    )
    lines.append(
        "  This is the TRUTH half of the gate and it says nothing about whether "
        "the thing is beautiful. Renders judge beauty, this judges truth, and "
        "BOTH have to pass before you call visual work done — so run "
        "render_preview or turntable and Read the picture as well."
    )
    if attention:
        lines.append(
            "  Name at most THREE of the flagged axes back, each with what fixes "
            "it. A list of every number here is a dump, not a critique."
        )
    return "\n".join(lines)


def _verify_detail_lines(axes: Mapping[str, Any]) -> list[str]:
    """The handful of numbers worth quoting under the axis summaries."""
    out: list[str] = []

    silhouette = axes.get("silhouette")
    if isinstance(silhouette, Mapping) and isinstance(silhouette.get("detail"), Mapping):
        detail = silhouette["detail"]
        iou = detail.get("iou") or {}
        aspect = detail.get("aspect_delta") or {}
        centroid = detail.get("centroid_delta") or {}
        mask = detail.get("reference_mask") or {}
        out.append(
            f"silhouette: IoU {fmt_number(iou.get('value'), 3)} "
            f"[{_tier_of(iou)}], proportions off "
            f"{fmt_number(aspect.get('value'), 3)}, centre off "
            f"{fmt_number(centroid.get('value'), 3)} of the frame; reference "
            f"mask from {mask.get('value')} [{_tier_of(mask)}]"
        )
        confidence = str(silhouette.get("confidence") or "")
        if confidence and confidence != "high":
            out.append(f"silhouette confidence: {confidence}")
        for reason in detail.get("confidence_reasons") or []:
            out.append(f"  why: {reason}")

    uv = axes.get("uv")
    if isinstance(uv, Mapping) and isinstance(uv.get("detail"), Mapping):
        detail = uv["detail"]
        islands = (detail.get("islands") or {}).get("value")
        flipped = (detail.get("flipped_faces") or {}).get("value")
        distortion = (detail.get("area_distortion") or {}).get("value")
        overlap = (detail.get("overlap") or {}).get("value")
        out.append(
            f"UVs: {fmt_number(islands, 0)} islands [measured], "
            f"{fmt_number(flipped, 0)} flipped [measured], worst density ratio "
            f"{fmt_number(distortion, 2)} [measured], overlap "
            f"{fmt_number(overlap, 0)} [heuristic]"
        )

    loops = axes.get("loops")
    if isinstance(loops, Mapping) and isinstance(loops.get("detail"), Mapping):
        detail = loops["detail"]
        zones = detail.get("zones") or []
        if zones:
            listed = ", ".join(
                f"{z.get('joint')}: {fmt_number((z.get('loops') or {}).get('value'), 0)}"
                for z in zones[:4]
            )
            out.append(
                f"loops at joints [heuristic, from {detail.get('source')}]: {listed}"
            )

    symmetry = axes.get("symmetry")
    if isinstance(symmetry, Mapping) and isinstance(symmetry.get("detail"), Mapping):
        detail = symmetry["detail"]
        out.append(
            f"mirror residual on {detail.get('axis')}: "
            f"{fmt_number((detail.get('mean_mm') or {}).get('value'), 2)} mm mean, "
            f"{fmt_number((detail.get('max_mm') or {}).get('value'), 2)} mm worst "
            "[measured] — REPORTED, never judged: asymmetry is usually a decision"
        )
    return out


def fmt_turntable_report(result: Mapping[str, Any]) -> str:
    """Where the contact sheet is, how to read it, and the two laws that go with it.

    The laws are here rather than only in the system prompt because this is the
    moment they apply. Order-swap de-biasing is not a nicety: ~26% of paired
    visual judgements reverse when the candidates change places, so an A/B read
    in one order is a coin flip dressed as a verdict.
    """
    path = str(result.get("path") or "")
    views = result.get("views")
    resolution = result.get("resolution")
    columns = result.get("columns")
    rows = result.get("rows")
    names = [str(n) for n in (result.get("objects") or [])]
    subject = ", ".join(names) if names and len(names) <= 4 else (
        f"{len(names)} objects" if names else "the scene"
    )

    lines = [
        f"Turntable of {subject}: {fmt_number(views, 0)} views at "
        f"{fmt_number(resolution, 0)} px, stitched into one "
        f"{fmt_number(columns, 0)} x {fmt_number(rows, 0)} contact sheet.",
        "",
        f"    {path}",
        "",
        "READ THAT FILE NOW. One image, every side. A single hero render is the "
        "cheapest way to be wrong about a mesh — it hides interpenetration, the "
        "flat side nobody modelled and the top of the head, which is exactly "
        "what a generated mesh gets wrong.",
    ]
    reading = result.get("reading_order")
    if reading:
        lines.append(f"  reading order: {reading}")
    lines.append(
        "  every tile is framed identically, so anything that changes between "
        "tiles is the MODEL changing and never the camera"
    )
    lines.append(
        "  when you look: does the silhouette read as the thing from EVERY "
        "side, or only from the one you rendered first? Is anything passing "
        "through anything? Is there a side with no detail on it at all?"
    )
    lines.append(
        "  ORDER-SWAP LAW — if you are comparing two of these (before and "
        "after, candidate A and candidate B), read them in BOTH orders. About a "
        "quarter of paired visual judgements reverse when the presentation "
        "order swaps. If your verdict flips, it is too close to call: say so "
        "and decide on the geometry instead."
    )
    lines.append(
        "  verify_design is the other half of this gate: renders judge beauty, "
        "it judges truth, and both have to pass."
    )
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
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
    # The geometric gate: a flow can end by MEASURING what it built, not just by
    # leaving a picture of it.
    "verify_design", "turntable",
    # PartForge
    "load_mesh", "load_meshes", "partforge_open",
    # imported meshes (Phase 6d)
    "check_model", "segment_model",
    # generated meshes (Phase 7 — meshgen writes a .glb, this brings it in)
    "import_generated",
    # base shapes and the component tree (Phase 11): the two samplers read a
    # drawn curve, and the merge is the end of a design — "scrap the collar,
    # then merge what's left and check it" is exactly the repeatable sequence
    # flows exist for (flows/merge-and-check.json).
    "profile_from_curve", "outline_from_curve", "merge_for_print",
    # RigForge
    "rigforge_list_tags", "rigforge_tag", "rigforge_untag", "rigforge_manifest",
    "rigforge_retopo", "rigforge_auto_uv", "rigforge_status", "rigforge_metarig",
    "rigforge_generate_rig", "rigforge_weights", "rigforge_export_godot",
    "rigforge_cloth", "rigforge_action", "rigforge_keyframe", "rigforge_retarget",
    "rig_check",
    # Mechanism demos (Phase 17): keyframe the press, light the LED, film it —
    # exactly the three-step sequence a flow exists to replay.
    "animate_object", "set_material_emission", "render_animation",
    # Floor plans (Phase 19): the plan file IS the model, so "build the level
    # again after I edited the plan" is one step a flow can end on — and in
    # `update` mode it is incremental, so replaying it is cheap and safe.
    "build_floorplan",
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


#: Where a project keeps the mesh files it owns. The assistant bridge's library
#: indexes this exact folder name (``bridge.MODELS_DIRNAME``), so a generation
#: filed here at birth is on the Library's Models row without anyone moving it.
PROJECT_MODELS_DIRNAME = "models"

#: How many `<stem>-N.glb` names are tried before giving up. A generation is
#: five minutes of GPU time; the one thing this must never do is write over the
#: previous one, and a folder with 999 attempts in it is a different problem.
_MODEL_NAME_ATTEMPTS = 999


def project_models_dir(project: Any) -> Path:
    """``projects/<slug>/models`` — the slug rules and the containment check.

    Both, and independently, exactly as ``project_paths`` does for the only
    other place in this server that writes files the artist did not name.
    """
    slug = project_slug(project)
    root = projects_root()
    folder = (root / slug / PROJECT_MODELS_DIRNAME).resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        raise ForgeError(
            f"{slug!r} would write outside {root}. Generated meshes only ever "
            "land in projects/<name>/models/."
        ) from None
    return folder


def generated_output_path(project: Any, image: Path) -> Path:
    """Where a generation lands when it is filed at birth: the absolute .glb.

    Named after the PICTURE, not the backend: the artist thinks in "the gecko",
    and `trellis2_00003.glb` is a filename that tells them which of four meshes
    is theirs only by opening all four. That is precisely the state the Library's
    Models row was invented to fix, and generating straight into a project is
    the version where it never happens in the first place.

    Never over a file that is already there. Five minutes of GPU work per file
    makes silently replacing one the worst thing this path could do.
    """
    folder = project_models_dir(project)
    stem = generated_object_name(image)
    candidate = folder / f"{stem}.glb"
    counter = 2
    while candidate.exists() and counter <= _MODEL_NAME_ATTEMPTS:
        candidate = folder / f"{stem}-{counter}.glb"
        counter += 1
    return candidate


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
    filed_to: str = "",
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
    if filed_to:
        lines.extend(fmt_filed_into(filed_to))
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


def fmt_filed_into(slug: str) -> List[str]:
    """The two lines that say a generation was born with a home.

    Worth saying out loud every time: the whole reason a mesh went missing from
    the shelf is that it landed in a scratch folder nothing owned, and an artist
    who is told where this one went does not have to go looking.
    """
    return [
        f"  filed into projects/{slug}/{PROJECT_MODELS_DIRNAME}/ — this mesh has a "
        "home from the moment it was written, rather than a scratch folder",
        "    it is on the Library tab's Models row already (web UI, Library, "
        "Models), badged with the project it belongs to",
    ]


def fmt_submitted_report(image: Path, submitted: Mapping[str, Any],
                         filed_to: str = "") -> str:
    """wait=false: the job id and how to follow it, with nothing pretended."""
    job_id = submitted.get("job_id")
    lines = [
        f"Started a 3D generation from {image.name}.",
        f"  job id: {job_id}",
        f"  backend: {submitted.get('backend')}   will write: {submitted.get('output')}",
    ]
    if filed_to:
        lines.extend(fmt_filed_into(filed_to))
    lines.extend([
        f'  Poll it with meshgen_status(job_id="{job_id}"). It takes about five '
        "minutes on this machine — say so before the artist starts waiting.",
        "  When it says done, import it with import_generated(path=<the file above>) "
        "— it arrives voxel-repaired, and check_model gives the print verdict.",
    ])
    return "\n".join(lines)


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


# --- Phase 11: base shapes, components and merge-for-print -------------------


def _points_block(points: Any, digits: int = 1) -> str:
    """A control-point list as the caller will paste it into a PARAMS block."""
    rows = []
    for pair in points or []:
        try:
            first, second = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append(f"({fmt_number(first, digits)}, {fmt_number(second, digits)})")
    return "[" + ", ".join(rows) + "]"


def fmt_profile_report(result: Mapping[str, Any]) -> str:
    """A drawn curve, measured — and the reminder that it is still parametric.

    The points ARE the answer, so they go first and in the shape they will be
    pasted in. Everything under them is the honest small print: which plane the
    stroke was read on, what was dropped, what soft_body will clamp.
    """
    points = result.get("points_mm") or []
    lines = [
        f"Read {result.get('object')} as a profile — {len(points)} control "
        f"points, {fmt_number(result.get('height_mm'), 1)} mm tall, widest "
        f"radius {fmt_number(result.get('max_radius_mm'), 1)} mm "
        f"({fmt_number(2 * float(result.get('max_radius_mm') or 0.0), 1)} mm "
        "across).",
        "",
        f"    profile_points = {_points_block(points)}",
        "",
        f"  drawn on the {result.get('plane')} plane (looking down "
        f"{result.get('plane_normal')}), sampled from "
        f"{fmt_number(result.get('sample_count'), 0)} points of a "
        f"{str(result.get('spline_type') or '').lower()} curve",
        "  radius first, then height — the pair forge_lib.soft_body takes. "
        "Nothing was built: write these into a PARAMS script (partforge_new_part) "
        "so every one of them stays a slider the artist can nudge.",
    ]
    if result.get("close_bottom"):
        lines.append("  the profile was dropped onto z = 0, so the body stands "
                     "on the plate")
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    lines.append(
        "  Then: partforge_generate, render_preview, and Read the render — a "
        "drawn curve is a proposal, and the first look is where it becomes right."
    )
    return "\n".join(lines)


def fmt_outline_report(result: Mapping[str, Any]) -> str:
    """A drawn loop, measured, for silhouette_part."""
    points = result.get("points_mm") or []
    lines = [
        f"Read {result.get('object')} as an outline — {len(points)} points, "
        f"{fmt_number(result.get('width_mm'), 1)} x "
        f"{fmt_number(result.get('height_mm'), 1)} mm.",
        "",
        f"    points = {_points_block(points)}",
        "",
        f"  drawn on the {result.get('plane')} plane, sampled from "
        f"{fmt_number(result.get('sample_count'), 0)} points of a "
        f"{str(result.get('spline_type') or '').lower()} curve",
        "  x then y, the outline forge_lib.silhouette_part extrudes. It models "
        "the part lying flat on the bed, so this is a proposal for one ear, "
        "fin, tail or crest — give it a thickness and, if it plugs into a body, "
        "a peg.",
    ]
    if result.get("recentered"):
        lines.append("  recentred on x = 0 with its bottom on y = 0, which is "
                     "where silhouette_part puts the peg")
    if result.get("self_intersections"):
        lines.append("  WARNING: the simplified outline crosses itself — "
                     "silhouette_part will refuse it. Ask for fewer points or "
                     "have the artist redraw the loop.")
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def fmt_merge_report(result: Mapping[str, Any]) -> str:
    """One shell out of many pieces, with the resolution trade said out loud."""
    sources = result.get("sources") or []
    voxel = result.get("voxel_size_mm")
    lines = [
        f"Merged {len(sources)} piece(s) into {result.get('object')} — "
        f"{fmt_number(result.get('face_count'), 0)} faces, "
        f"{fmt_number(result.get('vertex_count'), 0)} vertices, "
        f"{_dims(result.get('dimensions_mm'), 1)}.",
        f"  one shell at a {fmt_number(voxel, 2)} mm voxel"
        + (f" (half the printer's {fmt_number(result.get('nozzle_mm'), 2)} mm "
           "nozzle — two voxels per bead, which is every detail the printer "
           "could actually lay down)"
           if str(result.get("voxel_source")) == "nozzle" else "")
        + ".",
    ]
    if str(result.get("voxel_source")) == "coarsened":
        lines.append(f"  the voxel was coarsened to fit the face budget: detail "
                     f"finer than {fmt_number(voxel, 2)} mm is rounded off. Say "
                     "that to the artist — it is the trade, not a defect.")
    if str(result.get("voxel_source")) == "given":
        lines.append("  that size was asked for rather than derived from the nozzle")

    sealed = result.get("watertight_input_count")
    if isinstance(sealed, int) and sealed < len(sources):
        lines.append(f"  {len(sources) - sealed} of the pieces were not sealed on "
                     "their own; the merge closed them")
    if result.get("watertight") is False:
        lines.append("  the merged shell is NOT sealed — run mesh_diagnose to "
                     "see where before doing anything else")
    if result.get("kept_originals"):
        lines.append("  the originals are hidden, not deleted: nothing is lost "
                     "if the merge is wrong, and delete_object still scraps a "
                     "piece for good")
    else:
        lines.append("  the originals were DELETED, as asked")
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    lines.append(
        "  Now call check_model on it — a merged shell is a new mesh, and "
        "whether it still fits the bed and still has printable walls is a "
        "question only the check answers. If a check fails, mesh_diagnose "
        "locates the thin sculpted detail in millimetres so the artist knows "
        "WHERE to thicken."
    )
    return "\n".join(lines)


# --- Phase 10: maker mode — real parts, real circuits, real mechanisms -------
#
# Every renderer below takes a plain dict — a component record, a circuit plan,
# a plunger plan — and nothing else. They never import `maker`, never touch the
# service, and never raise on a missing key: a record that is half a record
# still renders the half it has. That is what lets them be tested against canned
# data, and it is also what stops a future field rename in service/components.py
# from turning a catalog listing into a traceback.

#: Width the wrapped honesty/purchase prose is folded to. The panel is narrow
#: and these are paragraphs, not numbers.
_MAKER_WRAP = 86

#: Repeated in the catalog header for each group. Kept beside the renderer so it
#: needs nothing but a dict to run.
_MAKER_CATEGORY_BLURB: Dict[str, str] = {
    "switch": "what the finger operates",
    "power": "what feeds it",
    "light": "what lights up",
    "fastener": "what holds the printed pieces together",
    "magnet": "what holds a door shut",
}


def _wrap_note(text: Any, indent: str) -> List[str]:
    """One prose field, folded to width under *indent*. Empty text, no lines."""
    body = " ".join(str(text or "").split())
    if not body:
        return []
    width = max(_MAKER_WRAP - len(indent), 30)
    lines: List[str] = []
    current = ""
    for word in body.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(indent + current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(indent + current)
    return lines


def _mm(value: Any, places: int = 2) -> str:
    """A measurement, trailing zeros trimmed — but only after the decimal point.

    Not :func:`fmt_number`: that one strips trailing zeros off the whole string,
    so ``fmt_number(500.0, 0)`` answers ``"5"``. Every force in grams-force and
    every current in milliamps here is a round hundred, so that is not a corner
    case, it is most of the catalog.
    """
    if value is None or isinstance(value, bool):
        return fmt_number(value, places)
    if isinstance(value, (int, float)):
        text = f"{float(value):.{places}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return fmt_number(value, places)


def _has(entry: Mapping[str, Any], *keys: str) -> bool:
    return all(entry.get(key) is not None for key in keys)


def _switch_dims(entry: Mapping[str, Any]) -> List[str]:
    dims: List[str] = []
    if _has(entry, "body_length_mm", "body_width_mm", "body_height_mm"):
        dims.append(
            f"{_mm(entry['body_length_mm'])} x {_mm(entry['body_width_mm'])} x "
            f"{_mm(entry['body_height_mm'])} mm body"
        )
    elif entry.get("body_diameter_mm") is not None:
        dims.append(f"{_mm(entry['body_diameter_mm'])} mm round body")
    if entry.get("overall_height_mm") is not None:
        dims.append(f"{_mm(entry['overall_height_mm'])} mm tall overall")
    if entry.get("panel_hole_diameter_mm") is not None:
        dims.append(
            f"mounts through a {_mm(entry['panel_hole_diameter_mm'])} mm panel hole"
        )
    if _has(entry, "panel_thickness_min_mm", "panel_thickness_max_mm"):
        dims.append(
            f"clamps a wall {_mm(entry['panel_thickness_min_mm'])}-"
            f"{_mm(entry['panel_thickness_max_mm'])} mm thick"
        )
    if entry.get("behind_panel_depth_mm") is not None:
        dims.append(
            f"{_mm(entry['behind_panel_depth_mm'])} mm of it sits behind the wall"
        )

    actuation = entry.get("actuation") or {}
    if actuation.get("stroke_mm") is not None:
        sideways = "slide" in str(entry.get("kind", ""))
        dims.append(
            f"{_mm(actuation['stroke_mm'])} mm of "
            + ("sideways throw" if sideways else "travel")
            + (
                f" at {_mm(actuation['force_gf'], 0)} gf"
                if actuation.get("force_gf") is not None
                else ""
            )
        )
    if actuation.get("latching") is not None:
        dims.append(
            "LATCHING (press on, press off - it stays where you put it)"
            if actuation["latching"]
            else "momentary (on only while held)"
        )
    if actuation.get("max_overtravel_mm") is not None:
        dims.append(
            f"max overtravel {_mm(actuation['max_overtravel_mm'])} mm - the number "
            "that caps a plunger's end stop"
        )
    electrical = entry.get("electrical") or {}
    if electrical.get("max_current_ma") is not None:
        dims.append(f"rated {_mm(electrical['max_current_ma'], 0)} mA")
    return dims


def _power_dims(entry: Mapping[str, Any]) -> List[str]:
    dims: List[str] = []
    if _has(entry, "diameter_mm", "thickness_mm"):
        dims.append(
            f"{_mm(entry['diameter_mm'])} mm across x "
            f"{_mm(entry['thickness_mm'])} mm thick"
        )
    if _has(entry, "body_length_mm", "body_width_mm", "body_height_mm"):
        dims.append(
            f"{_mm(entry['body_length_mm'])} x {_mm(entry['body_width_mm'])} x "
            f"{_mm(entry['body_height_mm'])} mm envelope"
        )
    electrical = entry.get("electrical") or {}
    if electrical.get("nominal_voltage_v") is not None:
        volts = f"{_mm(electrical['nominal_voltage_v'])} V"
        if electrical.get("fresh_voltage_v") is not None:
            volts += f" ({_mm(electrical['fresh_voltage_v'])} V fresh)"
        dims.append(volts)
    if electrical.get("capacity_mah") is not None:
        dims.append(f"{_mm(electrical['capacity_mah'], 0)} mAh")
    if electrical.get("recommended_current_ma") is not None:
        dims.append(f"comfortable at {_mm(electrical['recommended_current_ma'])} mA")
    if electrical.get("internal_resistance_ohm") is not None:
        dims.append(
            f"~{_mm(electrical['internal_resistance_ohm'], 0)} ohm internal "
            "resistance (this is what limits an LED wired straight across it)"
        )
    leads = entry.get("leads") or {}
    if leads.get("kind"):
        dims.append(f"{leads.get('count', '?')} x {leads['kind']}")
    return dims


def _light_dims(entry: Mapping[str, Any]) -> List[str]:
    dims: List[str] = []
    if entry.get("dome_diameter_mm") is not None:
        dims.append(f"{_mm(entry['dome_diameter_mm'])} mm lens")
    if entry.get("flange_diameter_mm") is not None:
        dims.append(
            f"{_mm(entry['flange_diameter_mm'])} mm FLANGE - the number that "
            "decides whether it seats or falls through"
        )
    if entry.get("above_flange_mm") is not None:
        dims.append(f"{_mm(entry['above_flange_mm'])} mm sticks out past the flange")
    electrical = entry.get("electrical") or {}
    if electrical.get("nominal_current_ma") is not None:
        dims.append(f"{_mm(electrical['nominal_current_ma'], 0)} mA nominal")
    if electrical.get("default_color"):
        dims.append(f"default colour {electrical['default_color']}")
    return dims


def _fastener_dims(entry: Mapping[str, Any]) -> List[str]:
    dims: List[str] = []
    if entry.get("thread"):
        dims.append(str(entry["thread"]))
    elif entry.get("thread_diameter_mm") is not None:
        dims.append(f"M{_mm(entry['thread_diameter_mm'], 0)} thread")
    if entry.get("length_mm") is not None:
        dims.append(f"{_mm(entry['length_mm'])} mm long")
    heads = entry.get("heads") or {}
    pan = heads.get("pan") or {}
    if pan.get("diameter_mm") is not None:
        dims.append(
            f"pan head {_mm(pan['diameter_mm'])} x {_mm(pan.get('height_mm'))} mm"
        )
    if entry.get("clearance_hole_normal_mm") is not None:
        dims.append(f"clearance hole {_mm(entry['clearance_hole_normal_mm'])} mm")
    if entry.get("thread_forming_hole_mm") is not None:
        dims.append(f"thread-forming hole {_mm(entry['thread_forming_hole_mm'])} mm")
    if _has(entry, "hole_diameter_mm", "hole_depth_mm"):
        dims.append(
            f"needs a {_mm(entry['hole_diameter_mm'])} x "
            f"{_mm(entry['hole_depth_mm'])} mm hole - the insert maker's number, "
            "with NO printer tolerance added to it"
        )
    return dims


def _magnet_dims(entry: Mapping[str, Any]) -> List[str]:
    dims: List[str] = []
    if _has(entry, "diameter_mm", "thickness_mm"):
        dims.append(
            f"{_mm(entry['diameter_mm'])} x {_mm(entry['thickness_mm'])} mm disc"
        )
    if entry.get("grade"):
        dims.append(str(entry["grade"]))
    if entry.get("pull_force_kg") is not None:
        dims.append(f"~{_mm(entry['pull_force_kg'])} kg pull")
    if entry.get("tolerance_mm") is not None:
        dims.append(f"held to +/-{_mm(entry['tolerance_mm'])} mm")
    if entry.get("max_temperature_c") is not None:
        dims.append(
            "loses its magnetism permanently above "
            f"{_mm(entry['max_temperature_c'], 0)} C"
        )
    return dims


_MAKER_DIMS = {
    "switch": _switch_dims,
    "power": _power_dims,
    "light": _light_dims,
    "fastener": _fastener_dims,
    "magnet": _magnet_dims,
}


def component_key_dims(entry: Mapping[str, Any]) -> List[str]:
    """The handful of numbers that decide whether a part fits the design."""
    renderer = _MAKER_DIMS.get(str(entry.get("category")))
    return renderer(entry) if renderer else []


def fmt_component_card(entry: Mapping[str, Any], clone_tolerance_mm: Any = None) -> str:
    """One component in full: what it is, every number, and the honesty fields."""
    name = entry.get("name", "?")
    lines = [f"{name}  ({entry.get('category', '?')}, {entry.get('kind', '?')})"]
    lines.extend(_wrap_note(entry.get("summary"), "  "))
    for dim in component_key_dims(entry):
        lines.append(f"    - {dim}")

    for label in ("actuation", "leads", "electrical"):
        block = entry.get(label) or {}
        if block.get("note"):
            lines.append(f"  {label}:")
            lines.extend(_wrap_note(block["note"], "    "))
    if entry.get("polarity"):
        lines.append("  polarity:")
        lines.extend(_wrap_note(entry["polarity"], "    "))
    if entry.get("grip_note"):
        lines.extend(_wrap_note(entry["grip_note"], "    "))
    if entry.get("datum"):
        lines.append("  where Z = 0 is, when you model around it:")
        lines.extend(_wrap_note(entry["datum"], "    "))
    styles = entry.get("mount_styles") or []
    if styles:
        lines.append(
            "  maker_lib.mount styles: " + ", ".join(str(one) for one in styles)
        )
    else:
        lines.append(
            "  no mount style - this one is held by a cutout or by another part"
        )
    if entry.get("purchase_note"):
        lines.append("  BUY:")
        lines.extend(_wrap_note(entry["purchase_note"], "    "))
    if entry.get("purchase_link"):
        # A search URL, not a listing: it lands on the right shelf and nothing
        # more. Hand it over as a link and say which it is.
        lines.append(f"    buy: {entry['purchase_link']}")
    if entry.get("verify_against_your_part"):
        lines.append("  VERIFY AGAINST YOUR PART (say this to the artist):")
        lines.extend(_wrap_note(entry["verify_against_your_part"], "    "))
    if entry.get("source"):
        lines.extend(_wrap_note(f"numbers from: {entry['source']}", "  "))
    if clone_tolerance_mm is not None:
        lines.extend(
            _wrap_note(
                "These are datasheet-typical for the family, not a measurement of "
                f"one unit. Clones vary by +/-{_mm(clone_tolerance_mm)} mm routinely.",
                "  ",
            )
        )
    return "\n".join(lines)


def fmt_component_catalog(
    result: Mapping[str, Any], clone_tolerance_mm: Any = None
) -> str:
    """The catalog by category - name, what it is, key dims, what to buy."""
    records = list(result.get("records") or [])
    match = str(result.get("match") or "all")
    query = result.get("query")

    if match == "name" and len(records) == 1:
        closing = _wrap_note(
            "Design around these numbers; never invent a cavity and hope. The next "
            "step is a PARAMS script that composes maker_lib around this part "
            "(docs/part-authoring.md section 7), then partforge_generate and "
            "partforge_check.",
            "  ",
        )
        return fmt_component_card(records[0], clone_tolerance_mm) + "\n\n" + "\n".join(
            closing
        )

    if match == "category":
        heading = f"Maker components - {query} ({len(records)} of them)"
    elif match == "search":
        heading = f"Maker components matching {query!r} ({len(records)} found)"
    else:
        heading = (
            f"Maker component catalog - {len(records)} real parts, all buyable today"
        )

    lines = [heading, ""]
    lines.extend(
        _wrap_note(
            "PICK THE PART FIRST, THEN MODEL TO ITS DIMENSIONS. A cavity invented "
            "from nothing fits nothing. Every number below is datasheet-typical "
            "for the family"
            + (
                ", not a measurement of one unit: clones vary by +/-"
                f"{_mm(clone_tolerance_mm)} mm routinely, so the artist verifies "
                "against the part in their hand before they print."
                if clone_tolerance_mm is not None
                else "."
            ),
            "",
        )
    )
    lines.append("")

    order = [str(one) for one in (result.get("categories") or ())]
    present = [str(rec.get("category")) for rec in records]
    for category in order + [one for one in present if one not in order]:
        group = [rec for rec in records if str(rec.get("category")) == category]
        if not group:
            continue
        blurb = _MAKER_CATEGORY_BLURB.get(category)
        lines.append(
            category.upper()
            + f" ({len(group)})"
            + (f" - {blurb}" if blurb else "")
        )
        for entry in group:
            lines.append(f"  {entry.get('name', '?')}")
            lines.extend(_wrap_note(entry.get("summary"), "    "))
            dims = component_key_dims(entry)
            if dims:
                lines.extend(_wrap_note("dims: " + "; ".join(dims), "      "))
            if entry.get("purchase_note"):
                lines.extend(
                    _wrap_note("buy: " + str(entry["purchase_note"]), "      ")
                )
            if entry.get("purchase_link"):
                lines.append(f"      link: {entry['purchase_link']}")
        lines.append("")

    lines.extend(
        _wrap_note(
            'maker_components("<name>") gives one part in full - every dimension, '
            "the datum its Z = 0 sits on, its mount styles, and the "
            "verify-against-your-part sentence that has to reach the artist.",
            "  ",
        )
    )
    lines.extend(
        _wrap_note(
            "Then: circuit_plan for the resistor question, plunger_plan for a push "
            "mechanic, partforge_new_part for the script (docs/part-authoring.md "
            "section 7 is the rulebook), and wiring_guide at handover.",
            "  ",
        )
    )
    return "\n".join(lines)


def _circuit_headline(plan: Mapping[str, Any]) -> str:
    led = plan.get("led") or {}
    supply = plan.get("supply") or {}
    switch = plan.get("switch") or None
    parts = [f"{led.get('color', '?')} {led.get('name', 'LED')}"]
    cells = supply.get("cells") or 1
    parts.append(
        str(supply.get("name") or "supply") + (f" x{cells}" if cells > 1 else "")
    )
    if switch:
        parts.append(str(switch.get("name")))
    return " + ".join(parts)


def fmt_circuit_plan(plan: Mapping[str, Any]) -> str:
    """The resistor answer, the arithmetic behind it, and what it costs."""
    led = plan.get("led") or {}
    supply = plan.get("supply") or {}
    switch = plan.get("switch") or None
    resistor = plan.get("resistor_ohms") or 0.0

    lines = [f"Circuit - {_circuit_headline(plan)}", ""]
    lines.append(f"  VERDICT: {str(plan.get('verdict', '?')).upper()}")
    lines.extend(_wrap_note(plan.get("reason"), "    "))
    lines.append("")

    band = led.get("forward_voltage_band_v") or []
    lines.append(
        f"  LED        {led.get('name', '?')} in {led.get('color', '?')} - drops "
        f"{_mm(led.get('forward_voltage_v'))} V"
        + (
            f" (a bag of them runs {_mm(band[0])}-{_mm(band[1])} V)"
            if len(band) == 2
            else ""
        )
    )
    lines.append(
        f"  supply     {supply.get('name') or 'bench supply'}"
        + (f" x{supply['cells']}" if (supply.get("cells") or 1) > 1 else "")
        + f" - {_mm(supply.get('voltage_v'))} V"
        + (
            f", {_mm(supply.get('capacity_mah'), 0)} mAh"
            if supply.get("capacity_mah")
            else ""
        )
        + (
            f", ~{_mm(supply.get('internal_resistance_ohm'), 0)} ohm internal"
            if supply.get("internal_resistance_ohm")
            else ""
        )
    )
    if switch:
        lines.append(
            f"  switch     {switch.get('name')} - "
            + (
                "latching (press on, press off)"
                if switch.get("latching")
                else "momentary (on while held)"
            )
            + f", rated {_mm(switch.get('max_current_ma'), 0)} mA"
        )
    lines.append(
        f"  headroom   {_mm(plan.get('headroom_v'))} V left for a resistor to drop"
    )
    if resistor:
        lines.append(
            f"  RESISTOR   {_mm(resistor, 0)} ohm, "
            f"{_mm(plan.get('resistor_rating_w'))} W (the exact answer is "
            f"{_mm(plan.get('resistor_exact_ohms'), 1)} ohm, rounded UP to the next "
            f"{plan.get('resistor_series', 'E12')} value - too big only dims the "
            "LED, too small cooks it)"
        )
    else:
        lines.append("  RESISTOR   none - there is no voltage left for one to drop")
    if plan.get("forward_current_ma") is not None:
        lines.append(
            f"  current    {_mm(plan.get('forward_current_ma'), 1)} mA through the LED"
        )
    if plan.get("runtime_hours"):
        lines.append(
            f"  runtime    roughly {_mm(plan.get('runtime_hours'), 1)} hours"
        )
        lines.extend(_wrap_note(plan.get("runtime_note"), "             "))
    if plan.get("resistor_gentle_ohms"):
        lines.append(
            f"  gentler    {_mm(plan['resistor_gentle_ohms'], 0)} ohm instead runs it "
            f"at {_mm(plan.get('resistor_gentle_current_ma'))} mA"
            + (
                f" for roughly {_mm(plan.get('resistor_gentle_runtime_h'), 1)} hours"
                if plan.get("resistor_gentle_runtime_h")
                else ""
            )
            + " - the cell's own comfortable draw rather than the LED's textbook 20 mA"
        )

    order = plan.get("series_order") or []
    if order:
        lines.append("")
        lines.extend(
            _wrap_note(
                "One loop, in series: "
                + " -> ".join(str(one) for one in order)
                + ". No branches, no second wire, nothing connected twice.",
                "  ",
            )
        )

    for note in plan.get("notes") or []:
        lines.append("")
        lines.extend(_wrap_note("note: " + str(note), "  "))
    for warning in plan.get("warnings") or []:
        lines.append("")
        lines.extend(_wrap_note("WARNING: " + str(warning), "  "))

    lines.append("")
    lines.extend(
        _wrap_note(
            'Say the verdict to the artist in plain words - "no resistor needed" is '
            "a real answer and a surprising one, so it earns the sentence saying "
            "why. Then wiring_guide for the soldering steps and the shopping list.",
            "  ",
        )
    )
    return "\n".join(lines)


def fmt_wiring_guide(guide: Mapping[str, Any]) -> str:
    """The soldering steps and the shopping list, in the artist's language."""
    plan = guide.get("plan") or {}
    steps = list(guide.get("steps") or [])
    bom = list(guide.get("bom") or [])

    lines = [f"Wiring guide - {_circuit_headline(plan)}", ""]
    lines.append(f"  {str(plan.get('verdict', '?')).upper()}")
    lines.extend(_wrap_note(plan.get("reason"), "    "))
    lines.append("")

    lines.append(f"SOLDER IT IN THIS ORDER ({len(steps)} steps)")
    for index, step in enumerate(steps, start=1):
        folded = _wrap_note(step, "")
        if not folded:
            continue
        lines.append(f"  {index:>2}. {folded[0]}")
        lines.extend(f"      {more}" for more in folded[1:])
    lines.append("")

    lines.append(f"SHOPPING LIST ({len(bom)} lines)")
    linked = 0
    for row in bom:
        quantity = row.get("quantity")
        lines.append(
            f"  {quantity if quantity is not None else 1} x {row.get('item', '?')}"
            + (f"  [{row['source']}]" if row.get("source") else "")
        )
        lines.extend(_wrap_note(row.get("note"), "      "))
        # The catalogue carries a search URL per line item. The dogfood run of
        # 2026-09-16 (finding G-2) saw an assistant hand over search STRINGS and
        # apologise for not having links, because this formatter dropped the
        # column the data was already in. Never paraphrase a link away.
        link = str(row.get("purchase_link") or "").strip()
        if link:
            linked += 1
            lines.append(f"      buy: {link}")
    lines.append("")
    if linked:
        lines.extend(
            _wrap_note(
                f"{linked} of the {len(bom)} lines carry a link. Put them in the "
                "reply as links — a search URL that lands on the right shelf is "
                "the whole difference between a shopping list and homework. They "
                "are searches, not listings, so say that: never promise a price, "
                "a seller or that a specific item is in stock, and NEVER buy "
                "anything or offer to.",
                "  ",
            )
        )
        lines.append("")

    lines.extend(
        _wrap_note(
            "THE RULE THAT SAVES THE MOST REWORK: test the whole loop on the "
            "bench, cell in and switch pressed, BEFORE a single drop of glue. "
            "Glue is the point of no return, and nineteen failures in twenty are "
            "the LED round the wrong way, the cell upside down, or the wrong pair "
            "of switch terminals.",
            "  ",
        )
    )
    lines.append("")
    lines.extend(
        _wrap_note(
            "Hand this over as the last thing in a functional build - after the "
            "pieces are checked and the artist knows what to print. Do not "
            "paraphrase the polarity step or the test step away; those two are "
            "the whole guide.",
            "  ",
        )
    )
    return "\n".join(lines)


def fmt_plunger_plan(plan: Mapping[str, Any]) -> str:
    """The push mechanic's kinematics, with the sentence to say to the artist."""
    lines = [
        f"Push mechanic - a {_mm(plan.get('stem_diameter_mm'))} mm plunger on a "
        f"{plan.get('switch', '?')}",
        "",
    ]
    lines.append(
        f"  travel        {_mm(plan.get('travel_mm'))} mm - the switch's own "
        f"{_mm(plan.get('switch_stroke_mm'))} mm stroke, plus "
        f"{_mm(plan.get('overtravel_mm'))} mm of overtravel, plus "
        f"{_mm(plan.get('free_play_mm'))} mm of designed free play"
    )
    lines.append(
        "  end stop      the cap's underside lands on the guide's top rim after "
        f"{_mm(plan.get('end_stop_gap_mm'))} mm"
    )
    lines.extend(_wrap_note(plan.get("end_stop_note"), "                "))
    lines.append(
        f"  guide         {_mm(plan.get('guide_length_mm'))} mm sleeve, "
        f"{_mm(plan.get('guide_outer_diameter_mm'))} mm outside, bore "
        f"{_mm(plan.get('bore_diameter_mm'))} mm "
        f"({_mm(plan.get('clearance_per_side_mm'))} mm per side, from "
        f"{plan.get('fit_source', 'the printer profile')})"
    )
    lines.append(
        f"  engagement    {_mm(plan.get('guide_engagement_mm'))} mm = "
        f"{_mm(plan.get('guide_engagement_ratio'), 1)} x the stem - under that a "
        "pin cocks in its bore and jams on the first off-centre push"
    )
    lines.append(
        f"  retention     a {_mm(plan.get('flange_diameter_mm'))} mm flange, wider "
        "than the bore, so the plunger cannot leave through the front"
    )
    if plan.get("keyed"):
        lines.append(
            "  anti-rotation the stem is keyed, so a shaped cap cannot spin on it"
        )
    if plan.get("force_note"):
        lines.append("  force:")
        lines.extend(_wrap_note(plan["force_note"], "    "))
    if plan.get("return"):
        lines.append("  the return:")
        lines.extend(_wrap_note(plan["return"], "    "))
    if plan.get("print_orientation"):
        lines.append("  printing:")
        lines.extend(_wrap_note(plan["print_orientation"], "    "))

    clamped = plan.get("clamped") or []
    if clamped:
        lines.append("")
        lines.append(
            "  CLAMPED - say every one of these out loud, do not swallow them:"
        )
        for item in clamped:
            lines.extend(_wrap_note("- " + str(item), "    "))
    for note in plan.get("notes") or []:
        lines.extend(_wrap_note("note: " + str(note), "  "))

    assembly = plan.get("assembly") or []
    if assembly:
        lines.append("")
        lines.append("  ASSEMBLY, IN ORDER (the order IS the mechanism):")
        for index, step in enumerate(assembly, start=1):
            folded = _wrap_note(step, "")
            if not folded:
                continue
            lines.append(f"    {index}. {folded[0]}")
            lines.extend(f"       {more}" for more in folded[1:])

    lines.append("")
    lines.extend(
        _wrap_note(
            "These numbers are the reply, not the appendix. Tell the artist what "
            "the press FEELS like in their own words - how far it moves, what "
            "pushes it back, and where it stops - then build the geometry with "
            "maker_lib.plunger() inside a PARAMS script (docs/part-authoring.md "
            "section 7.4). This tool does the arithmetic; partforge_generate "
            "makes the solid.",
            "  ",
        )
    )
    return "\n".join(lines)


# --- Phase 15: the project's own .blend file ---------------------------------
#
# Two renderers, and each one carries one sentence the artist cannot check for
# themselves.
#
# The save's is that their own file did not move. `save_as_mainfile(copy=True)`
# is the whole promise of that command, and a promise nobody repeats is a
# promise nobody trusts — so `session_file` (where THEIR Ctrl+S still writes)
# is printed rather than summarised, and the impossible case (`retargeted`) is
# shouted rather than dropped.
#
# The open's is what a file load would throw away. `needs_confirmation` is a
# QUESTION, not a failure and not a step to get past: nothing was touched, and
# the only correct next move is to put the sentence to the artist and wait. So
# the renderer says that in the imperative, offers the save as the way to lose
# nothing either way, and never once mentions confirm=true as a thing to do
# now.


def fmt_project_save_report(result: Mapping[str, Any]) -> str:
    """`save_project_blend`, with the untouched-file promise said out loud."""
    project = str(result.get("project") or "the project")
    path = str(result.get("path") or "")
    count = result.get("object_count")
    size = result.get("size")

    head = f"Saved {fmt_number(count, 0)} object(s) into {path}"
    if result.get("replaced"):
        head += " (replacing the previous save)"
    if isinstance(size, (int, float)) and size:
        head += f" — {fmt_size(size)}"
    lines = [head + "."]

    session = str(result.get("session_file") or "")
    lines.append(
        "  It is a COPY. The artist's own file did not move: this Blender "
        + (f"session still saves to {session}"
           if session else "session is still unsaved, exactly as it was")
        + " — File > Save goes where it always went. Say that; it is the "
        "reason this is safe to press."
    )
    if result.get("retargeted"):
        lines.append(
            "  WARNING: Blender reported that it RETARGETED this session to the "
            "project file. Tell the artist immediately and have them use File > "
            "Save As to put their own file back before they save again."
        )
    if result.get("created_folder"):
        lines.append(f"  the projects/{project}/ folder did not exist and was "
                     "made for it")
    names = result.get("objects") or []
    if names:
        lines.append(f"  saved: {fmt_name_list(names, 12)}")
    lines.append(
        f"  Point them at it: the **{project}** card in the Library now has an "
        "**Open** button, and that button loads this scene — the sculpt, the "
        "lighting, the reference empties — straight back into Blender. In "
        "Blender itself the same save is **press N → Forge tab → PartForge box "
        "→ Save Scene to Project**."
    )
    return "\n".join(lines)


def fmt_project_open_report(result: Mapping[str, Any]) -> str:
    """`open_project_blend` — the question, or the world replaced.

    The confirmation branch is the important one, and it is written at the
    model rather than at the artist: this is the moment where a helpful
    assistant discards an afternoon of somebody's sculpting by being agreeable.
    """
    project = str(result.get("project") or "the project")
    path = str(result.get("path") or "")

    if result.get("needs_confirmation"):
        lost = str(result.get("would_lose") or
                   "This session has unsaved changes.")
        session = str(result.get("session_file") or "")
        lines = [
            f"NOT opened — nothing was touched. {path} is there, but loading it "
            "would throw away unsaved work in this session.",
            f"  what would be lost: {lost}",
        ]
        if session:
            lines.append(f"  the session's own file: {session}")
        hint = str(result.get("hint") or "")
        if hint:
            lines.append(f"  Blender's own hint: {hint}")
        lines.append(
            "  RELAY THIS AS A QUESTION AND STOP. Repeat what would be lost in "
            "the artist's own words and wait for a plain yes before calling "
            "this again with confirm=true. Do not confirm on your own and do "
            "not confirm because they asked to open it — they asked before "
            "they knew the cost. A file load resets Blender's undo stack, so "
            "Ctrl+Z cannot bring the scene back afterwards."
        )
        lines.append(
            f"  Offer the save first: save_project_blend keeps this scene as "
            f"projects/{project}/{project}.blend (a copy — their own file is "
            "untouched), and then opening costs nothing at all."
        )
        return "\n".join(lines)

    count = result.get("object_count")
    lines = [
        f"Opened {path} — {fmt_number(count, 0)} object(s) in the scene now.",
    ]
    discarded = str(result.get("discarded") or "")
    if discarded:
        lines.append(f"  discarded, as confirmed: {discarded}")
    session = str(result.get("session_file") or "")
    if session:
        lines.append(f"  this session is now that file: {session}")
    if result.get("server_running"):
        port = result.get("server_port")
        lines.append(
            "  the Forge server kept listening across the file load"
            + (f" (port {fmt_number(port, 0)})" if port else "")
            + " — you can carry straight on"
        )
    else:
        lines.append(
            "  the Forge server is NOT listening after the load: tell them to "
            "press N → Forge tab → Start under Forge Server before you try "
            "anything else"
        )
    names = result.get("objects") or []
    if names:
        lines.append(f"  in the scene: {fmt_name_list(names, 12)}")
    lines.append(
        "  Say two things and stop: what came back, and that **undo does not "
        "cross a file load** — Ctrl+Z will not return to the previous scene. "
        "Then get_scene_info before you act on anything, because every object "
        "name you knew a moment ago belonged to a different file."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 17 — mechanism demos (animate_object / set_material_emission /
# render_animation)
# ---------------------------------------------------------------------------
#
# The robotics-site trick, in three commands: a part that presses, a light that
# comes on, and a short film of the two happening together. Everything here is
# playback of numbers the maker tools already computed — `plunger_plan` says the
# cap travels 2.1 mm, and the demo shows 2.1 mm of travel. Nothing simulates a
# force, a spring rate or a collision, and the formatters below say so, because
# a film that looks like physics and is not is the one way this feature could
# mislead an artist about their own design.

#: The channels one object key may move. `location_mm` leads because every
#: number in this toolchain is a millimetre: the plan says 2.1 mm and the key
#: says 2.1, with nothing to convert and nothing to get wrong.
OBJECT_KEY_CHANNELS = ("location_mm", "location", "rotation_euler_deg", "scale")

#: The honesty sentence, kept here so the report carries it even when an older
#: add-on result has no `honesty` field of its own.
MECHANISM_HONESTY = (
    "This is an ILLUSTRATION of the intended motion, not a simulation: nothing "
    "here computes a force, a spring rate or a collision. Say that to the "
    "artist in your own words every time you show one."
)


def _normalize_object_key(entry: Any, index: int) -> Dict[str, Any]:
    """One `animate_object` key, checked before anything reaches Blender.

    The add-on parses the whole batch before it applies any of it, so that a
    typo in `keys[7]` cannot leave `keys[0..6]` half done. This is the same rule
    one layer earlier, where the error can name the tool.
    """
    where = f"keys[{index}]"
    if not isinstance(entry, Mapping):
        raise ForgeError(
            f"{where} must be an object like "
            '{"frame": 8, "location_mm": [0, 0, -2.1]}; '
            f"got {entry!r}."
        )
    key = dict(entry)  # unknown fields survive, the way pose keys' do

    if key.get("frame") is None:
        raise ForgeError(
            f"{where} has no `frame`. Every key names the frame it sits on — "
            "that is what makes it a key rather than a pose."
        )
    key["frame"] = _as_frame(key.get("frame"), where)

    if key.get("location") is not None and key.get("location_mm") is not None:
        raise ForgeError(
            f"{where} gives both `location` (metres) and `location_mm` "
            "(millimetres). They are the same channel — use one. In a maker "
            "demo that is almost always location_mm, because the build plan is "
            "in millimetres."
        )

    moved = []
    for channel in OBJECT_KEY_CHANNELS:
        if key.get(channel) is None:
            key.pop(channel, None)
            continue
        key[channel] = _as_vector3(key[channel], f"{where} {channel}")
        moved.append(channel)
    if not moved:
        raise ForgeError(
            f"{where} (frame {key['frame']}) sets no channel. Give it at least "
            "one of location_mm (millimetres — the unit the build plan is in), "
            "location (metres), rotation_euler_deg or scale; a key that changes "
            "nothing keyframes nothing."
        )
    return key


def normalize_object_keys(keys: Any) -> List[Dict[str, Any]]:
    """The `animate_object` batch, validated entry by entry."""
    if isinstance(keys, Mapping):  # one key, unwrapped
        keys = [keys]
    if isinstance(keys, (str, bytes)) or not isinstance(keys, (list, tuple)):
        raise ForgeError(
            "`keys` must be a list of keyframe objects like "
            '[{"frame": 1, "location_mm": [0, 0, 0]}, '
            '{"frame": 8, "location_mm": [0, 0, -2.1]}]; '
            f"got {keys!r}."
        )
    out = [_normalize_object_key(entry, index) for index, entry in enumerate(keys)]
    if not out:
        raise ForgeError(
            "`keys` was empty. A press stroke is three keys: at rest, pressed, "
            'back — [{"frame": 1, "location_mm": [0, 0, 0]}, '
            '{"frame": 8, "location_mm": [0, 0, -2.1]}, '
            '{"frame": 20, "location_mm": [0, 0, 0]}].'
        )
    return out


def object_keys_frame_range(
    keys: Sequence[Mapping[str, Any]]
) -> Optional[Tuple[int, int]]:
    """(first, last) frame across a normalized object-key list."""
    frames = [int(key["frame"]) for key in keys if isinstance(key.get("frame"), int)]
    return (min(frames), max(frames)) if frames else None


#: Emission strength. The add-on's own ceiling, mirrored so the refusal reads
#: the same on either side of the socket.
MAX_EMISSION_STRENGTH = 1000.0


def normalize_emission_strength(strength: Any) -> float:
    """How bright, or a refusal naming the range."""
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise ForgeError(
            f"strength must be a number (got {strength!r}). 0 is off; an LED "
            "reads at about 5-20 in EEVEE."
        )
    value = float(strength)
    if not 0.0 <= value <= MAX_EMISSION_STRENGTH:
        raise ForgeError(
            f"strength must be between 0 and "
            f"{fmt_number(MAX_EMISSION_STRENGTH, 0)} "
            f"(got {fmt_number(value, 2)}). 0 is the LED off; 5-20 is an LED on."
        )
    return value


def normalize_emission_color(color: Any) -> Optional[List[float]]:
    """`[r, g, b]` in 0-1, or `None` when the colour is being left alone."""
    if color is None:
        return None
    values = _as_vector3(color, "color")
    for component in values:
        if not 0.0 <= component <= 1.0:
            raise ForgeError(
                f"color components are 0-1, not 0-255 (got {values}). A warm "
                "amber LED is about [1.0, 0.62, 0.2]."
            )
    return values


#: The demo's own numbers, mirrored from the add-on so a refusal costs no round
#: trip and reads identically on either side.
ANIMATION_FPS = 24
ANIMATION_MIN_FPS = 1
ANIMATION_MAX_FPS = 60
ANIMATION_RESOLUTION = 640
ANIMATION_MIN_RESOLUTION = 128
ANIMATION_MAX_RESOLUTION = 1920
#: A mechanism demo is a couple of seconds of a thing moving. Past this a
#: headless render stops being something the artist waits for.
ANIMATION_MAX_FRAMES = 600

#: Where a project keeps its renders, by the folder convention in
#: docs/architecture.md ("spec.json, part.py, exports/ and renders"). The
#: assistant bridge's Library plays the `.mp4` files in this exact folder
#: (`bridge.DEMOS_DIRNAME`), so a demo written here is on the artist's shelf
#: without anybody moving a file.
PROJECT_RENDERS_DIRNAME = "renders"

#: Bumped per demo inside one server process, like the preview counter: a demo
#: is compared against the one before it, so both files have to still exist.
_animation_counter = 0


def normalize_animation_fps(fps: Any) -> int:
    """Frames per second, or a refusal naming the range and the default."""
    if fps is None:
        return ANIMATION_FPS
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise ForgeError(f"fps must be a whole number (got {fps!r}).")
    if isinstance(fps, float) and not float(fps).is_integer():
        raise ForgeError(f"fps must be a whole number (got {fps}).")
    value = int(fps)
    if not ANIMATION_MIN_FPS <= value <= ANIMATION_MAX_FPS:
        raise ForgeError(
            f"fps must be between {ANIMATION_MIN_FPS} and {ANIMATION_MAX_FPS} "
            f"(got {value}). {ANIMATION_FPS} is the default and is right for a "
            "mechanism demo."
        )
    return value


def normalize_animation_resolution(resolution: Any) -> int:
    """Square pixels for the film, or a refusal naming the range."""
    if resolution is None:
        return ANIMATION_RESOLUTION
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)):
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution!r})."
        )
    if isinstance(resolution, float) and not float(resolution).is_integer():
        raise ForgeError(
            f"resolution must be a whole number of pixels (got {resolution})."
        )
    value = int(resolution)
    if not ANIMATION_MIN_RESOLUTION <= value <= ANIMATION_MAX_RESOLUTION:
        raise ForgeError(
            f"resolution must be between {ANIMATION_MIN_RESOLUTION} and "
            f"{ANIMATION_MAX_RESOLUTION} pixels (got {value}). The default, "
            f"{ANIMATION_RESOLUTION}, reads a 2 mm stroke and renders in "
            "seconds rather than minutes."
        )
    return value


def normalize_animation_frames(frame_start: Any, frame_end: Any) -> Tuple[int, int]:
    """`(start, end)`, in order and within the length a demo may be."""
    start = _as_frame(frame_start, "frame_start")
    end = _as_frame(frame_end, "frame_end")
    if end < start:
        raise ForgeError(
            f"frame_end ({end}) is before frame_start ({start}). Render the "
            "range the keys are on — animate_object reports it as frame_range."
        )
    count = end - start + 1
    if count > ANIMATION_MAX_FRAMES:
        raise ForgeError(
            f"{count} frames is longer than one demo renders in a go "
            f"({ANIMATION_MAX_FRAMES}). A mechanism demo is a couple of seconds "
            "— shorten the range, or drop the fps."
        )
    return start, end


def animation_name(name: Any) -> str:
    """`"Litwick press!"` -> `"litwick-press"`, or refuse.

    A NAME, never a path — `project_slug`'s rules, for `project_slug`'s reason:
    a caller who wrote a separator meant a location, and quietly writing
    somewhere else is worse than an error. A trailing `.mp4` is dropped rather
    than refused, because writing it is the obvious thing to do and the
    extension is not the caller's to choose here.
    """
    raw = "" if name is None else str(name).strip().strip('"').strip()
    if raw.lower().endswith(".mp4"):
        raw = raw[:-4].strip()
    if not raw:
        raise ForgeError(
            "The demo's name was empty. Name it for what it shows — "
            '"litwick press" becomes litwick-press.mp4 — or omit `name` and one '
            "is chosen."
        )
    for marker in _TRAVERSAL_MARKERS:
        if marker in raw:
            raise ForgeError(
                f"{name!r} is not a demo name — it looks like a path (it "
                f"contains {marker!r}). A demo is always written into the "
                "project's renders/ folder, so pass just a name, e.g. "
                '"litwick press".'
            )
    slug = _SLUG_SEPARATORS.sub("-", raw.lower()).strip("-")
    if not slug:
        raise ForgeError(
            f"{name!r} has no letters or digits in it, so it cannot name a "
            'file. Try something like "litwick press".'
        )
    return slug[:60].rstrip("-")


def animation_path(project: Any = None, view: str = "iso",
                   name: Any = None) -> Path:
    """Where this demo is written — chosen HERE, never asked of the caller.

    `render_preview`'s rule, for `render_preview`'s reason: an agent that picks
    its own output paths picks them inconsistently, and a path parameter is a
    way to write an .mp4 anywhere on the artist's disk. With a `project` the
    film lands in `projects/<slug>/renders/`, which is where the Library plays
    it from; without one it lands in the same scratch folder previews use, so an
    exploratory demo costs no project folder.

    `name` chooses the FILE's name inside that folder and nothing else — it is
    slugged, never joined — so "litwick press" is `litwick-press.mp4` and a
    re-render of the same demo replaces it rather than leaving take after take.
    Unnamed demos are numbered instead, so two exploratory films in one session
    are two files.
    """
    global _animation_counter

    if name is not None and str(name).strip():
        stem = animation_name(name)
    else:
        _animation_counter += 1
        stem = f"demo-{_animation_counter:03d}-{str(view or 'iso').strip().lower()}"
    if project is None or not str(project).strip():
        path = Path(config.PREVIEWS_DIR) / f"{stem}.mp4"
    else:
        slug = project_slug(project)
        root = projects_root()
        folder = (root / slug / PROJECT_RENDERS_DIRNAME).resolve()
        try:
            folder.relative_to(root)
        except ValueError:
            raise ForgeError(
                f"{slug!r} would write outside {root}. Demos only ever land in "
                "projects/<name>/renders/."
            ) from None
        path = folder / f"{stem}.mp4"
    ensure_parent_dir(path)
    return path


def fmt_animate_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    """What was keyed, over what span, and what to do with it next."""
    name = result.get("object") or subject
    action = result.get("action") or "(unnamed)"
    lines = [
        f"Keyframed {name} — {summary}",
        f"  action: {action}   "
        f"{fmt_counted('keys set', result.get('keys_set'))}   "
        f"frames {fmt_frame_range(result.get('frame_range'))}",
    ]
    channels = result.get("channels")
    if channels:
        lines.append(f"  channels: {fmt_name_list(channels, 6)}")
    if result.get("cleared_fcurves"):
        lines.append(
            f"  cleared first: {fmt_number(result.get('cleared_fcurves'), 0)} "
            "existing curve(s), so this replaces the motion rather than "
            "layering onto it"
        )
    lines.extend(fmt_warnings(result.get("warnings")))
    lines.append(
        "  next: set_material_emission for anything that lights up, then "
        "render_animation over this frame range to get the film."
    )
    lines.append("  " + MECHANISM_HONESTY)
    return "\n".join(lines)


def fmt_emission_report(subject: str, result: Mapping[str, Any], summary: str) -> str:
    """The LED: which material, how bright, and whether it was keyed."""
    name = result.get("object") or subject
    material = result.get("material") or "(unnamed)"
    lines = [
        f"Emission set on {name} — {summary}",
        f"  material: {material}"
        + ("  (new)" if result.get("created_material") else "")
        + f"   strength: {fmt_number(result.get('strength'), 2)}",
    ]
    if result.get("keyframed"):
        lines.append(
            f"  keyed at frame {fmt_number(result.get('frame'), 0)} "
            f"({result.get('interpolation') or 'CONSTANT'}): "
            + fmt_name_list(result.get("keyed"), 4)
        )
    else:
        lines.append(
            "  not keyed — this is a state, not an event. Pass `frame` to key "
            "the moment it comes on."
        )
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    lines.append(
        "  it only shows in a render with materials: render_preview"
        '(shading="material") or render_animation(engine="eevee"). Workbench '
        "draws clay and will not show it."
    )
    return "\n".join(lines)


def fmt_animation_report(result: Mapping[str, Any], summary: str) -> str:
    """Where the film is, and the instruction to hand the artist the path.

    Deliberately NOT "Read that file": the Read tool renders bitmaps, and an
    .mp4 is the one thing this server produces that the model cannot look at.
    What it CAN do is name the path, which is what makes the demo play inline in
    the artist's chat and on the project's Library card.
    """
    path = str(result.get("path") or "")
    lines = [
        f"Rendered the demo — {summary}",
        "",
        f"    {path}",
        "",
        "NAME THAT FULL PATH IN YOUR REPLY. It plays inline in the artist's "
        "chat and on the project's card in the Library; a film they cannot "
        "press play on is a filename.",
    ]
    frames = result.get("frames")
    fps = result.get("fps")
    duration = result.get("duration_s")
    if frames or fps:
        lines.append(
            f"  {fmt_number(frames, 0)} frames at {fmt_number(fps, 0)} fps"
            + (f" — {fmt_number(duration, 1)} s of film" if duration else "")
            + f", {fmt_number(result.get('resolution'), 0)} px, "
            + f"{result.get('engine') or '?'}"
        )
    size = result.get("size_bytes")
    if size:
        lines.append(f"  {fmt_number(float(size) / 1024.0, 0)} KB on disk")
    if result.get("framed_all_visible"):
        lines.append(
            "  every visible mesh is in frame; pass `objects` to film one piece"
        )
    if result.get("framed_over_frames"):
        lines.append(
            "  the camera was fitted over "
            f"{fmt_name_list(result.get('framed_over_frames'), 8)} — the whole "
            "clip, so nothing presses itself out of shot"
        )
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    lines.append("  " + str(result.get("honesty") or MECHANISM_HONESTY))
    return "\n".join(lines)


#: How many joints one `rig_check` report names in full. The whole result is one
#: object per joint per pose; the agent needs the gate, the failures and their
#: worst three numbers — feed it the computed features, never the raw report.
RIG_CHECK_LISTED = 12


def fmt_rig_check_report(result: Mapping[str, Any], summary: str) -> str:
    """The deformation gate: the verdict, the joints that earned it, the caveat."""
    gate = str(result.get("gate") or "?")
    lines = [
        f"Deformation check — gate: {gate.upper()} ({summary})",
        f"  {fmt_number(result.get('joints_measured'), 0)} joint(s) measured in "
        f"{fmt_number(result.get('seconds'), 2)} s"
        + (f", {fmt_number(result.get('poses_run'), 0)} poses"
           if result.get("poses_run") else ""),
    ]
    says = str(result.get("says") or "").strip()
    if says:
        lines.append(f"  {says}")
    lines.extend(fmt_warnings(result.get("warnings")))

    def _measured(value: Any, places: int = 1) -> str:
        # A joint the harness could not measure reports None, and "None%" would
        # read as a number. "-" reads as what it is: no measurement.
        return "-" if value is None else fmt_number(value, places)

    joints = [j for j in (result.get("joints") or []) if isinstance(j, Mapping)]
    rank = {"fail": 0, "attention": 1, "pass": 2}
    joints.sort(key=lambda j: rank.get(str(j.get("verdict")), 3))
    for joint in joints[:RIG_CHECK_LISTED]:
        lines.append(
            "  {label:<14} vol={vol:>6}%  twist={twist:>6}%  "
            "new clips={clips:>4}   {verdict}".format(
                label=str(joint.get("label") or joint.get("joint") or "?")[:14],
                vol=_measured(joint.get("worst_volume_loss_pct")),
                twist=_measured(joint.get("worst_twist_collapse_pct")),
                clips=_measured(joint.get("worst_new_intersections"), 0),
                verdict=str(joint.get("verdict") or "?"),
            )
        )
    if len(joints) > RIG_CHECK_LISTED:
        lines.append(f"  ... and {len(joints) - RIG_CHECK_LISTED} more joint(s)")

    skipped = [s for s in (result.get("joints_skipped") or [])
               if isinstance(s, Mapping)]
    for entry in skipped[:4]:
        lines.append(
            f"  NOT MEASURED — {entry.get('label') or entry.get('joint')}: "
            f"{entry.get('reason')}"
        )
    if len(skipped) > 4:
        lines.append(f"  ... and {len(skipped) - 4} more unmeasured joint(s)")

    if result.get("pose_restored"):
        lines.append("  the pose was restored: the rig is exactly as they left it")
    tier = str(result.get("threshold_tier") or "").strip()
    lines.append(
        "  THE THRESHOLDS ARE HEURISTICS, not facts about their work"
        + (f" — {tier}" if tier else "")
        + ". Report the numbers next to the band that judged them, and say a "
        "fail is a band you can argue with."
    )
    if gate != "pass":
        lines.append(
            "  next: the usual cause is weights, not bones — rigforge_weights"
            '(action="report") on the mesh, then cleanup, then check again.'
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 19 — floor plans (floorplan_validate / floorplan_build / floorplan_diff)
# ---------------------------------------------------------------------------
#
# THE PLAN FILE IS THE MODEL. Everything in this block exists to keep that true:
# the plan lives on disk in the design folder beside the sheet it belongs to, it
# is read back rather than reconstructed from a conversation, and every id in it
# is forever, because the id is what names the Blender object and therefore what
# makes an edit incremental instead of a full regen.
#
# The write here is `save_design_doc`'s write, deliberately: same slug rules,
# same filename alphabet, same second check of the resolved path against the
# folder it must be inside, same parse-before-write. A floor plan is a design
# document that happens to be machine-readable, and giving it a second, laxer
# door onto projects/ would be the bug.

#: The plan file, and the picture of it the artist actually signs off on. Both
#: live in `projects/<slug>/design/` — the plan is not a build artefact, it is
#: the design, and it outlives every greybox built from it.
FLOORPLAN_FILENAME = "floorplan.json"
FLOORPLAN_SVG_FILENAME = "floorplan.svg"

#: The greybox honesty line, mirrored from the add-on (`floorplan.HONESTY`) so
#: the report carries it even when an older add-on result has no `honesty` field
#: of its own — exactly `MECHANISM_HONESTY`'s arrangement, for exactly its
#: reason: the one way this feature could mislead somebody is by looking like a
#: building when it is a sketch of one.
FLOORPLAN_HONESTY = (
    "This is a prototype greybox at real sizes, not a construction drawing: "
    "walls are boxes on the plan's centrelines, openings are rectangular "
    "cutouts, every fixture is a plain box at its stated footprint, and nothing "
    "here is framed, structural, code-compliant or load-bearing. Say that to "
    "the artist in your own words every time you show one."
)

#: The sentence that ends a validate report. The echo-back gate is Phase 19's
#: whole safety story — the artist corrects the DIAGRAM, never the mesh — so the
#: tool that produces the numbers says so rather than leaving it to the prompt.
FLOORPLAN_GATE = (
    "nothing is built by this call. Hand-author design/floorplan.svg from these "
    "numbers, name its full path in your reply so they can LOOK at your reading "
    "of their drawing, and call floorplan_build only after they say yes."
)

#: What each list in a plan holds, and the word a report calls one of them.
#: `labels` are "fixtures" in every sentence a human reads — the washer, the
#: sofa — and `label` only in the schema, so the plural is spelled rather than
#: derived from the key.
FLOORPLAN_NOUNS: Dict[str, Tuple[str, str]] = {
    "room": ("room", "rooms"),
    "wall": ("wall", "walls"),
    "opening": ("opening", "openings"),
    "label": ("fixture", "fixtures"),
}

#: Ids named in full in a diff report before it starts counting instead. A diff
#: the model cannot quote back id by id is a diff that becomes "some walls".
FLOORPLAN_IDS_LISTED = 12


def floorplan_path(project: Any) -> Tuple[str, Path]:
    """``(slug, projects/<slug>/design/floorplan.json)``, checked both ways."""
    slug = project_slug(project)
    name = design_filename(FLOORPLAN_FILENAME)  # belt and braces, one alphabet
    _design, path = design_paths(slug, name)
    return slug, path


def read_floorplan(project: Any) -> Tuple[str, Path, Dict[str, Any]]:
    """The project's saved plan — ``(slug, path, plan)`` — or a refusal.

    A missing plan is the commonest case and gets the sentence that says what to
    do about it, not a stack trace: the plan is authored by the assistant with
    `save_design_doc`, so "there is no plan yet" means "write one".
    """
    slug, path = floorplan_path(project)
    if not path.is_file():
        raise ForgeError(
            f"{slug} has no saved floor plan ({path} does not exist). The plan "
            "file IS the model here: author it against the schema and save it "
            f'with save_design_doc("{slug}", "{FLOORPLAN_FILENAME}", ...) — ids '
            "on every room, wall, opening and fixture, because an id is what "
            "survives an edit."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ForgeError(f"Could not read {path}: {exc}") from exc
    try:
        plan = json.loads(text)
    except ValueError as exc:
        raise ForgeError(
            f"{path} is not valid JSON ({exc}). Nothing was changed — fix the "
            "file, or save a corrected plan over it with save_design_doc."
        ) from None
    if not isinstance(plan, dict):
        raise ForgeError(
            f"{path} holds a {type(plan).__name__}, not a plan object. A floor "
            'plan is {"version": 1, "units": "mm", "defaults": {...}, "rooms": '
            '[...], "walls": [...], "labels": [...]}.'
        )
    return slug, path, plan


def write_floorplan(project: Any, plan: Mapping[str, Any]) -> Tuple[Path, bool]:
    """Save a plan into the design folder — ``(path, overwritten)``.

    `save_design_doc`'s rules, reached through `save_design_doc`'s helpers, so
    there is one implementation of "may these bytes land here" rather than two.
    """
    slug, path = floorplan_path(project)
    try:
        body = json.dumps(plan, indent=2, allow_nan=False, sort_keys=False)
    except (TypeError, ValueError) as exc:
        raise ForgeError(
            f"That plan will not serialise to JSON ({exc}), so it was not "
            "saved. A plan is a file: every value in it has to be a string, "
            "number, boolean, list or object — and never NaN or Infinity."
        ) from exc
    text = normalize_design_content(body, FLOORPLAN_FILENAME)
    design = design_root(slug)
    try:
        design.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ForgeError(f"Could not write {path}: {exc}") from exc
    return path, existed


def fmt_plan_counts(tally: Mapping[str, int]) -> str:
    """`3 rooms, 12 walls, 4 openings, 2 fixtures` — always all four."""
    parts = []
    for kind, (one, many) in FLOORPLAN_NOUNS.items():
        count = int(tally.get(kind, 0) or 0)
        parts.append(f"{count} {one if count == 1 else many}")
    return ", ".join(parts)


def fmt_plan_noun_phrase(ids: Sequence[str], kinds: Mapping[str, str]) -> str:
    """`2 walls and 1 fixture` from a list of ids and what each one is."""
    tally: Dict[str, int] = {}
    for ident in ids:
        kind = kinds.get(str(ident)) or "entry"
        tally[kind] = tally.get(kind, 0) + 1
    if not tally:
        return "nothing"
    parts = []
    for kind in list(FLOORPLAN_NOUNS) + sorted(k for k in tally
                                               if k not in FLOORPLAN_NOUNS):
        count = tally.get(kind)
        if not count:
            continue
        one, many = FLOORPLAN_NOUNS.get(kind, (kind, kind + "s"))
        parts.append(f"{count} {one if count == 1 else many}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def fmt_plan_ids(ids: Sequence[str], limit: int = FLOORPLAN_IDS_LISTED) -> str:
    """`wall-03, wall-04` — the ids themselves, because ids are the contract."""
    listed = [str(ident) for ident in ids]
    if not listed:
        return "none"
    if len(listed) <= limit:
        return ", ".join(listed)
    return ", ".join(listed[:limit]) + f", +{len(listed) - limit} more"


def fmt_plan_defaults(defaults: Mapping[str, Any]) -> str:
    """The resolved `defaults` block as one readable line of millimetres."""
    def mm(key: str) -> str:
        return fmt_number(defaults.get(key), 1)

    return (
        f"ceiling {mm('ceiling_mm')} mm, wall {mm('wall_mm')} mm, "
        f"door {mm('door_w_mm')} x {mm('door_h_mm')} mm, "
        f"window {mm('window_w_mm')} x {mm('window_h_mm')} mm at sill "
        f"{mm('sill_mm')} mm, fixture height {mm('label_h_mm')} mm, "
        f"floor slab {mm('floor_mm')} mm, footprint anchor "
        f"{defaults.get('label_anchor') or 'center'}"
    )


def fmt_floorplan_validated(
    *,
    slug: Optional[str],
    source: str,
    resolved: Mapping[str, Any],
    tally: Mapping[str, int],
    matches: Sequence[Mapping[str, Any]],
    defaulted: Sequence[Tuple[str, Sequence[str]]],
    saved: Optional[Path],
    overwritten: bool,
) -> str:
    """The plan as Forge read it: the counts, the numbers it filled in, the gate.

    Written to be QUOTED. Every appliance match carries its confidence and the
    route it came in by, because "I read 'W/D' as a washer/dryer, 850 mm tall,
    on an alias match" is a sentence the artist can correct in one word — and
    correcting it on the sheet costs nothing, while correcting it after the
    level is built costs a rebuild and possibly their hand edits.
    """
    lines = [
        f"Floor plan reads clean ({source}) — {fmt_plan_counts(tally)}."
    ]
    if saved is not None:
        lines.append(
            f"  {'updated' if overwritten else 'saved'} the normalised plan — "
            f"{saved}"
        )
        lines.append(
            "  normalised, not resolved: the defaults block stays live on disk, "
            "so a number they never chose stays a default and raising "
            "ceiling_mm later still moves every wall that takes its height "
            "from it."
        )
    lines.append(f"  defaults: {fmt_plan_defaults(resolved.get('defaults') or {})}")

    if defaulted:
        shown = [f"{ident} ({', '.join(fields)})" for ident, fields in defaulted]
        head = shown[:FLOORPLAN_IDS_LISTED]
        extra = len(shown) - len(head)
        lines.append(
            f"  took a default ({len(shown)}): " + "; ".join(head)
            + (f"; +{extra} more" if extra > 0 else "")
        )
    else:
        lines.append("  every number is the artist's own — nothing was defaulted")

    known = [m for m in matches if m.get("match")]
    unknown = [m for m in matches if not m.get("match")]
    for match in known[:FLOORPLAN_IDS_LISTED]:
        lines.append(
            f"  {match.get('id')}: {match.get('label')!r} -> "
            f"{match.get('match')} ({match.get('how')}, confidence "
            f"{fmt_number(match.get('confidence'), 2)}) — "
            # places=1: `fmt_number` strips trailing zeros, so 850.0 at 0
            # decimal places would print as "85".
            f"{fmt_number(match.get('height_mm'), 1)} mm tall"
            + (f", height from the {match.get('height_from')} table"
               if match.get("height_from") == "appliance" else "")
        )
    if len(known) > FLOORPLAN_IDS_LISTED:
        lines.append(f"  ... and {len(known) - FLOORPLAN_IDS_LISTED} more matched")
    if unknown:
        lines.append(
            "  no appliance match, so the footprint they DREW is the size "
            f"({len(unknown)}): "
            + fmt_plan_ids([f"{m.get('id')} {m.get('label')!r}" for m in unknown])
        )
    lines.append(
        "  THE FOOTPRINT IS NEVER OVERRIDDEN — a plan is top-down and has no "
        "height in it, so only the height ever comes from the table."
    )
    if slug:
        lines.append(
            f"  next: floorplan_diff before any later edit, so you can say which "
            f"ids rebuild; floorplan_build to materialise it in Blender."
        )
    lines.append("  " + FLOORPLAN_GATE)
    return "\n".join(lines)


def fmt_floorplan_diff(
    summary: Mapping[str, Any],
    *,
    old_source: str,
    new_source: str,
) -> str:
    """What this edit touches, by id — the sentence to say BEFORE building.

    The whole point of Phase 19's stable ids is that an edit is a diff and not a
    regen, and the artist only gets that guarantee if somebody tells them: "only
    wall-03 rebuilds" is a promise about the hand-sculpted sofa in the next room
    surviving, and it is worth more than any amount of the level being right.
    """
    kinds = summary.get("kinds") or {}
    added = [str(i) for i in (summary.get("added") or [])]
    removed = [str(i) for i in (summary.get("removed") or [])]
    changed = [str(i) for i in (summary.get("changed") or [])]
    unchanged = [str(i) for i in (summary.get("unchanged") or [])]
    touched = len(added) + len(removed) + len(changed)

    lines = [
        f"Floor-plan diff ({old_source} -> {new_source}) — {touched} id(s) "
        f"touched, {len(unchanged)} left alone.",
        f"  rebuilds ({len(changed)}): {fmt_plan_ids(changed)}",
        f"  adds ({len(added)}): {fmt_plan_ids(added)}",
        f"  deletes ({len(removed)}): {fmt_plan_ids(removed)}",
        f"  unchanged ({len(unchanged)}): {fmt_plan_ids(unchanged)}",
    ]

    if not touched:
        lines.append(
            "  nothing changed: floorplan_build would touch not one object. "
            "Say that rather than building again."
        )
    else:
        clauses = []
        if changed:
            clauses.append(f"rebuilds {fmt_plan_noun_phrase(changed, kinds)}")
        if added:
            clauses.append(f"adds {fmt_plan_noun_phrase(added, kinds)}")
        if removed:
            clauses.append(f"deletes {fmt_plan_noun_phrase(removed, kinds)}")
        sentence = ", ".join(clauses)
        joiner = ", and touches" if len(clauses) > 1 else " and touches"
        lines.append(
            f"  SAY THIS BEFORE YOU BUILD: \"this edit {sentence}{joiner} "
            "nothing else\" — naming the ids."
        )
    lines.append(
        "  an opening's change is its wall's change (a door is a hole cut in "
        "one), so a moved door lists the door AND the wall it moved on."
    )
    lines.append(
        "  every id not named above keeps the object it already has, hand edits "
        "included. That is what the ids are for, and it is the promise to make "
        "out loud."
    )
    return "\n".join(lines)


def fmt_floorplan_build_report(result: Mapping[str, Any], summary: str) -> str:
    """The greybox: what moved, what Forge refused to touch, how big it came out."""
    collection = result.get("collection") or "Floorplan"
    built = [str(i) for i in (result.get("built") or [])]
    updated = [str(i) for i in (result.get("updated") or [])]
    deleted = [str(i) for i in (result.get("deleted") or [])]
    unchanged = [str(i) for i in (result.get("unchanged") or [])]
    kept = [k for k in (result.get("kept") or []) if isinstance(k, Mapping)]

    lines = [
        f"Built the greybox level in '{collection}' — {summary}",
        f"  built {len(built)}   updated {len(updated)}   "
        f"deleted {len(deleted)}   unchanged {len(unchanged)}   "
        f"kept {len(kept)}",
    ]
    if built:
        lines.append(f"  built: {fmt_plan_ids(built)}")
    if updated:
        lines.append(f"  rebuilt: {fmt_plan_ids(updated)}")
    if deleted:
        lines.append(f"  deleted: {fmt_plan_ids(deleted)}")
    if unchanged:
        lines.append(
            f"  NOT TOUCHED ({len(unchanged)}): {fmt_plan_ids(unchanged)} — same "
            "object, same mesh, same transform"
        )
    for entry in kept[:8]:
        lines.append(
            f"  KEPT, not rebuilt — {entry.get('object') or entry.get('id')}: "
            f"{entry.get('why')}"
        )
    if len(kept) > 8:
        lines.append(f"  ... and {len(kept) - 8} more kept object(s)")

    lines.append(
        f"  {fmt_number(result.get('objects'), 0)} object(s), "
        f"{fmt_number(result.get('pieces'), 0)} piece(s) — "
        f"{fmt_number(result.get('walls'), 0)} wall(s) with "
        f"{fmt_number(result.get('openings'), 0)} opening(s), "
        f"{fmt_number(result.get('fixtures'), 0)} fixture(s), "
        f"{fmt_number(result.get('floors'), 0)} floor slab(s) over "
        f"{fmt_number(result.get('rooms'), 0)} room(s)"
    )

    size = (result.get("bounds_mm") or {}).get("size") \
        if isinstance(result.get("bounds_mm"), Mapping) else None
    size = size or result.get("dimensions_mm")
    if isinstance(size, (list, tuple)) and len(size) == 3:
        # places=1, never 0: `fmt_number` strips trailing zeros, so a float
        # formatted to no decimals turns 4100.0 into "41".
        lines.append(
            f"  bounds: {fmt_number(size[0], 1)} x {fmt_number(size[1], 1)} x "
            f"{fmt_number(size[2], 1)} mm"
        )

    mechanisms = [m for m in (result.get("mechanisms") or [])
                  if isinstance(m, Mapping)]
    if mechanisms:
        lines.append(f"  door mechanisms ({len(mechanisms)}):")
        for record in mechanisms[:6]:
            lines.append(
                f"    {record.get('id') or record.get('opening')} on "
                f"{record.get('wall')}: {record.get('joint_type')} about "
                f"{fmt_vector(record.get('axis'), 0)}, "
                f"{fmt_number(record.get('range_deg'), 1)} deg, swing "
                f"{record.get('swing') or 'unset'} / hinge "
                f"{record.get('hinge') or 'unset'}"
            )
        if len(mechanisms) > 6:
            lines.append(f"    ... and {len(mechanisms) - 6} more")
        lines.append(
            "    DATA ONLY: no leaf object was built and nothing is rigged."
        )

    lines.extend(fmt_warnings(result.get("warnings")))
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    lines.append(
        "  every placeholder is a component SLOT: elaborate it parametrically, "
        "swap in a generated or imported mesh at the slot's footprint, or "
        "sculpt it. Promotion is one-way and the level never rebuilds around "
        "it — an edited object is KEPT with a warning, never clobbered."
    )
    lines.append("  " + str(result.get("honesty") or FLOORPLAN_HONESTY))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 12: molds and casting — "print the mold, pour the copies"
#
# The service has had four routes and a real undercut analysis since Phase 12
# (/mold, /export_mold, /mold_mesh, /export_mold_mesh, service/undercut.py) and
# no tool reached any of them. The 2026-09-16 dogfood run (finding G-1) spent
# 170 seconds of one turn reading Forge's own source and then hand-wrote a flow
# with absolute machine paths in it to get at them. Everything below is the
# reporting half of the two tools that close that hole; the geometry, the
# thresholds and the pour instructions are all the service's own.
# ---------------------------------------------------------------------------

#: Where a project keeps its molds, beside `renders/` and `models/`. The mold
#: halves are printed files like any other, so they are filed in the project
#: rather than in scratch — the dogfood run's two STLs landed here by hand, and
#: the artist could not find them afterwards.
PROJECT_MOLDS_DIRNAME = "molds"

#: The two things a mold can be. `printed_negative` is the two-half box with the
#: part cut out of it; `master_box` prints the figure untouched plus an open box
#: to pour silicone around. The undercut analysis is what chooses between them.
MOLD_MODES = ("printed_negative", "master_box")

#: Said out loud on every mold report, for the same reason `MECHANISM_HONESTY`
#: is: the numbers are measured and the VERDICT is a judgement call about
#: typical silicone. The service ships its own sentence in
#: `undercuts.criterion.approximation`; this is the framing around it.
MOLD_HONESTY = (
    "Undercut severity is a JUDGEMENT about typical tin/platinum silicone, not "
    "a simulation of it: the angles and areas are measured off the real "
    "triangles, but the line between mild and severe is a threshold somebody "
    "chose. Quote the number beside the band that judged it, and say which "
    "rubber you are assuming."
)

#: The casting BOM the service cannot know about, because it is not geometry.
#: Named in the report so a mold turn ends with what to buy, the way a maker
#: turn does — and, like `wiring_guide`'s list, it is a search, never a basket.
MOLD_SILICONE_BOM = "https://www.smooth-on.com/category/mold-making-silicone-rubber/"


def project_molds_dir(project: Any) -> Path:
    """``projects/<slug>/molds`` — the slug rules and the containment check.

    Both, and independently, exactly as ``project_models_dir`` does. A mold is
    written where the artist can find it beside the part it came off, never to a
    path the model made up: the dogfood run's hand-written flow baked an
    absolute machine path into a repo file, which is the failure this prevents.
    """
    slug = project_slug(project)
    root = projects_root()
    folder = (root / slug / PROJECT_MOLDS_DIRNAME).resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        raise ForgeError(
            f"{slug!r} would write outside {root}. Molds only ever land in "
            "projects/<name>/molds/."
        ) from None
    return folder


def mold_basename(name: Any, default: str = "mold") -> str:
    """The file-name stem for a mold's pieces: a NAME, never a path.

    ``project_slug``'s rules for ``project_slug``'s reason — the service joins
    this onto its own directory (``<basename>_mold_top.stl``), so a separator in
    it is an attempt to write somewhere else.
    """
    raw = "" if name is None else str(name).strip().strip('"').strip()
    if not raw:
        return default
    for marker in _TRAVERSAL_MARKERS:
        if marker in raw:
            raise ForgeError(
                f"{name!r} is not a mold name — it looks like a path (it "
                f"contains {marker!r}). The mold is always written into the "
                "project's molds/ folder, so pass just a name, e.g. "
                '"litwick flame".'
            )
    slug = _SLUG_SEPARATORS.sub("-", raw.lower()).strip("-")
    if not slug:
        raise ForgeError(
            f"{name!r} has no letters or digits in it, so it cannot name a "
            'file. Try something like "litwick flame".'
        )
    return slug[:60].rstrip("-")


def normalize_mold_mode(mode: Any) -> str:
    """``"printed_negative"`` / ``"master_box"``, or a refusal naming both."""
    if mode is None:
        return "printed_negative"
    value = str(mode).strip().lower().replace("-", "_").replace(" ", "_")
    if not value:
        return "printed_negative"
    if value not in MOLD_MODES:
        raise ForgeError(
            f"mode must be one of {', '.join(MOLD_MODES)} (got {mode!r}). "
            '"printed_negative" is two printed halves with the part cut out of '
            'them; "master_box" prints the figure plus an open box and you pour '
            "silicone around it, which is what severe undercuts need."
        )
    return value


def mold_severity(undercuts: Any) -> str:
    """The worse of the two halves, as a plain word. ``"unknown"`` if absent."""
    if not isinstance(undercuts, Mapping):
        return "unknown"
    severity = str(undercuts.get("severity") or "").strip().lower()
    return severity or "unknown"


def fmt_mold_mesh_input(mesh_input: Mapping[str, Any]) -> str:
    """One line about the triangles that arrived and what had to be fixed."""
    parts = [f"mesh in: {fmt_number(mesh_input.get('triangle_count'), 0)} triangle(s)"]
    source = mesh_input.get("source") or mesh_input.get("file_path")
    if source:
        parts.append(f"from {source}")
    repairs = mesh_input.get("repairs") or mesh_input.get("welded")
    if repairs:
        parts.append(f"repaired: {repairs}")
    return ", ".join(str(one) for one in parts)


def _mold_half_line(name: str, half: Mapping[str, Any]) -> str:
    """One half of the undercut report as a single scannable row."""
    severity = str(half.get("severity") or "?").upper()
    return (
        f"  {name:<12.12} {severity:<7.7} "
        f"{fmt_number(half.get('opposing_area_mm2'), 1)} mm2 of opposing faces "
        f"in {fmt_number(half.get('patch_count'), 0)} patch(es), worst "
        f"{fmt_number(half.get('max_angle_deg'), 2)} deg past vertical with "
        f"{fmt_number(half.get('max_depth_mm'), 2)} mm of sideways grip"
    )


def fmt_undercut_report(subject: str, payload: Mapping[str, Any]) -> str:
    """Will this thing come out of a mold? The analysis, before anything is cut.

    Reads the ``undercuts`` block every mold response carries
    (``service/undercut.py``) and nothing else, so one renderer serves a script
    and a mesh.
    """
    undercuts = payload.get("undercuts")
    if not isinstance(undercuts, Mapping):
        return (
            f"Moldability of {subject}: the service answered without an undercut "
            "block, which should not happen. Treat that as unknown rather than "
            "as a pass, and run it again."
        )

    severity = mold_severity(undercuts)
    lines = [
        f"Moldability of {subject} — {severity.upper()} undercuts",
        f"  {str(undercuts.get('verdict') or '?')}",
    ]

    parting = payload.get("parting_z_mm", undercuts.get("parting_z_mm"))
    source = str(payload.get("parting_source") or "").strip()
    lines.append(
        f"  parting plane: z = {fmt_number(parting, 2)} mm"
        + (f" ({source})" if source else "")
        + "   draw: mold_top lifts +Z, mold_bottom lifts -Z"
    )

    halves = undercuts.get("halves")
    if isinstance(halves, Mapping):
        for name in ("mold_top", "mold_bottom"):
            half = halves.get(name)
            if not isinstance(half, Mapping):
                continue
            lines.append(_mold_half_line(name, half))
            lines.extend(_wrap_note(half.get("detail"), "      "))
            examples = [
                one for one in (half.get("examples") or []) if isinstance(one, Mapping)
            ][:3]
            for example in examples:
                lines.append(
                    f"      look at {fmt_vector(example.get('position_mm'), 1)} mm — "
                    f"{fmt_number(example.get('angle_deg'), 1)} deg, "
                    f"{fmt_number(example.get('depth_mm'), 2)} mm deep"
                )

    if undercuts.get("recommend_master_box"):
        lines.append("  VERDICT: a two-part printed mold will NOT open on this.")
        lines.extend(_wrap_note(undercuts.get("recommendation"), "    "))
        lines.append(
            '    make_mold(mode="master_box") is the answer: print the figure '
            "itself, print an open box round it, pour silicone in, and the "
            "rubber flexes off what a rigid half cannot."
        )
    else:
        lines.append(
            "  VERDICT: a two-part printed mold opens on this — make_mold() "
            '(mode="printed_negative", the default) is the right call. '
            "recommend_master_box: false."
        )

    criterion = undercuts.get("criterion")
    if isinstance(criterion, Mapping):
        lines.append(
            "  judged at: anything more than "
            f"{fmt_number(criterion.get('threshold_deg'), 2)} deg past vertical "
            "counts as opposing; 'severe' needs a patch past "
            f"{fmt_number(criterion.get('mild_angle_deg'), 0)} deg AND deeper than "
            f"{fmt_number(criterion.get('severe_min_depth_mm'), 2)} mm"
        )
        lines.extend(_wrap_note(criterion.get("approximation"), "    "))
    lines.append("  " + MOLD_HONESTY)

    mesh_input = payload.get("mesh_input")
    if isinstance(mesh_input, Mapping):
        lines.append(f"  {fmt_mold_mesh_input(mesh_input)}")

    lines.append(
        "  Nothing was written: this is the analysis, not the mold. Say the "
        "verdict to the artist in plain words, then make_mold when they want "
        "the files."
    )
    return "\n".join(lines)


def fmt_mold_report(
    subject: str,
    payload: Mapping[str, Any],
    directory: Any,
    printer_source: str,
) -> str:
    """The mold that was written: what it is, what fights it, how to cast from it.

    Every file path is on its own line and the pour instructions are printed in
    full rather than summarised — they are the half of this the artist actually
    performs, and a build turn that never names its files reads to them as a
    turn that produced nothing (dogfood 2026-09-16, turn 11).
    """
    mode = str(payload.get("mode") or "printed_negative")
    pieces = [one for one in (payload.get("pieces") or []) if isinstance(one, Mapping)]
    files = [one for one in (payload.get("files") or []) if isinstance(one, Mapping)]

    lines = [
        f"Molded {subject} — {mode}, {len(pieces)} piece(s) into "
        f"{payload.get('directory', directory)}",
        f"  printer: {printer_source}",
    ]

    if mode == "master_box":
        box = payload.get("master_box")
        if isinstance(box, Mapping):
            lines.append(
                f"  pour box: {fmt_vector(box.get('size_mm'), 1)} mm, "
                f"{fmt_number(box.get('silicone_volume_ml'), 0)} ml of silicone to "
                f"fill it, split: {'yes' if box.get('split') else 'no'}"
            )
    else:
        draft = payload.get("draft") or {}
        keys = payload.get("registration_keys") or {}
        spout = payload.get("spout")
        vents = payload.get("vents") or {}
        lines.append(
            f"  parting plane: z = {fmt_number(payload.get('parting_z_mm'), 2)} mm "
            f"({payload.get('parting_source') or 'auto'})   "
            f"draft: {fmt_number(draft.get('angle_deg'), 1)} deg"
        )
        lines.append(
            f"  {fmt_number(keys.get('count'), 0)} registration key(s) "
            f"({fmt_number(keys.get('radius_mm'), 2)} mm — bumps on "
            f"{keys.get('male_half', 'mold_top')} into dimples in "
            f"{keys.get('female_half', 'mold_bottom')})   "
            + (
                f"spout {fmt_number(spout.get('diameter_mm'), 1)} mm"
                if isinstance(spout, Mapping)
                else "no spout"
            )
            + f"   {fmt_number(vents.get('count'), 0)} vent(s)"
        )

    for piece in pieces:
        stats = piece.get("stats") or {}
        lines.append(
            f"    {str(piece.get('name', '?')):<14.14} "
            f"{fmt_vector(stats.get('bounding_box_mm'), 1)} mm, "
            f"{fmt_number(piece.get('volume_mm3'), 0)} mm3 of plastic"
        )

    severity = mold_severity(payload.get("undercuts"))
    if severity != "unknown":
        undercuts = payload.get("undercuts") or {}
        lines.append(f"  undercuts: {severity.upper()} — {undercuts.get('verdict')}")
        lines.extend(_wrap_note(undercuts.get("detail"), "    "))
        if undercuts.get("recommend_master_box") and mode != "master_box":
            lines.append(
                "    WARNING: these halves are written, but this part's undercuts "
                "say a rigid mold will not come off it. Say so before they print "
                '— mode="master_box" and silicone is the version that works.'
            )
        lines.append("  " + MOLD_HONESTY)

    if files:
        lines.append("")
        lines.append(
            fmt_written_files(
                [
                    {
                        "name": one.get("name"),
                        "kind": one.get("kind") or one.get("format"),
                        "path": one.get("path"),
                    }
                    for one in files
                ],
                None,
            )
        )
        lines.append(
            "  NAME EVERY ONE OF THOSE PATHS IN YOUR REPLY. A file the artist is "
            "not told about is a file they do not have: the bridge turns a named "
            "path into something they can open, and an unnamed one into nothing."
        )

    instructions = [str(one) for one in (payload.get("instructions") or []) if one]
    if instructions:
        lines.append("")
        lines.append(f"HOW TO CAST FROM IT ({len(instructions)} steps)")
        for index, step in enumerate(instructions, start=1):
            folded = _wrap_note(step, "")
            if not folded:
                continue
            lines.append(f"  {index:>2}. {folded[0]}")
            lines.extend(f"      {more}" for more in folded[1:])

    lines.append("")
    lines.extend(
        _wrap_note(
            "WHAT THEY STILL HAVE TO BUY: none of this is in their hands yet. A "
            "silicone mold needs rubber, and a casting needs resin, and neither "
            "is a printed part. Put that line in the reply the way a wiring "
            "shopping list goes in, with the link, as a search rather than a "
            "promise about price or stock: " + MOLD_SILICONE_BOM + " . Never buy "
            "anything and never offer to.",
            "  ",
        )
    )

    mesh_input = payload.get("mesh_input")
    if isinstance(mesh_input, Mapping):
        lines.append(f"  {fmt_mold_mesh_input(mesh_input)}")
    return "\n".join(lines)


#: Where an object pulled out of Blender is parked on its way to the service.
#: Scratch, like the previews folder and for the same reason: it is an input to
#: a job, not something the artist keeps. The service reads it off disk (its own
#: `file_path` input), so it never crosses the MCP wire as triangles. The folder
#: itself is `config.MOLD_INPUT_DIR` (FORGE_MOLD_INPUT_DIR); this is the leaf
#: name that default is built from.
MOLD_INPUT_DIRNAME = "forge-mold-input"


def mold_input_path(name: Any) -> Path:
    """A scratch .stl to hand the service when the mold's input is in Blender.

    Unique per export, like `preview_path` and for B-4's reason: a mold of the
    body and a mold of the flame must never be the same file on disk.
    """
    global _mold_input_counter

    _mold_input_counter += 1
    stem = _SLUG_SEPARATORS.sub("-", str(name or "object").lower()).strip("-")
    stem = (stem or "object")[:40]
    path = Path(config.MOLD_INPUT_DIR) / (
        f"{stem}-{_mold_input_counter:03d}-{_PREVIEW_RUN_TAG}.stl"
    )
    ensure_parent_dir(path)
    return path


_mold_input_counter = 0
