"""PartForge: the parametric side of the add-on.

Holds the scene-level project state (script path, parameter schema and current
values), talks to the Build123d geometry service over plain HTTP (``urllib``,
no third-party dependencies) and feeds the returned mesh through the same
``load_mesh`` code path the socket protocol uses.

Every request runs on a worker thread and is delivered back to the main thread
through a ``bpy.app.timers`` callback, so the Blender UI never blocks on the
service.  All requests carry an explicit timeout.
"""

import json
import os
import threading
import traceback
import urllib.error
import urllib.request

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..prefs import get_prefs, pref, service_url
from . import common
from .registry import ForgeError, command

# unit -> UI kind
_UNIT_KINDS = {
    "mm": "FLOAT",
    "in": "FLOAT",
    "deg": "FLOAT",
    "ratio": "FLOAT",
    "count": "INT",
    "bool": "BOOL",
}

EXPORT_FORMATS = (
    ("stl", "STL", "Triangle mesh for slicing"),
    ("step", "STEP", "Solid CAD exchange (Fusion, FreeCAD)"),
    ("3mf", "3MF", "Modern print format with units and metadata"),
)

JOINT_TYPES = (
    ("dovetail", "Dovetail", "Sliding trapezoidal rail; nothing extra to print or buy"),
    ("pin", "Pin", "Sockets in both faces plus a printed pin per pair"),
    ("magnet", "Magnet", "Pockets in both faces for a disc magnet; nothing printed"),
    ("none", "None", "A plain cut with no joint"),
)

SEGMENT_MODES = (
    ("AUTO", "Auto", "Let the service pick from the bed-fit suggestion"),
    ("RADIAL", "Radial", "N equal wedges about Z, for rings and round parts"),
    ("PLANAR", "Planar", "Slabs cut at the given Z heights"),
)

#: check status -> (icon, short label). CANCEL is the hard stop, ERROR the warning.
CHECK_ICONS = {
    "pass": "CHECKMARK",
    "warn": "ERROR",
    "fail": "CANCEL",
}


def split_phrase(suggestion):
    """``Prints as 4 radial pieces`` for a feasible bed_fit segmentation.

    A part is designed at the size it should be; bed fit is print planning, not
    a design constraint. So the row's hint says how the part PRINTS rather than
    telling the artist their part is too big to make.
    """
    mode = suggestion.get("mode") if isinstance(suggestion, dict) else None
    kind = str(suggestion.get("kind") or "").strip() if isinstance(suggestion, dict) else ""
    count = None
    if isinstance(mode, dict):
        radial = mode.get("radial")
        planar = mode.get("planar")
        if isinstance(radial, (int, float)) and not isinstance(radial, bool):
            count = int(radial)
            kind = kind or "radial"
        elif isinstance(planar, (list, tuple)):
            count = len(planar) + 1
            kind = kind or "planar"
    if count is None or count < 2:
        return "Prints in more than one piece"
    return ("Prints as %d %s pieces" % (count, kind)).replace("  ", " ")


def suggested_split_label(props):
    """The Segments box's one-line nudge, in print-planning language.

    ``props.suggested_mode`` is the raw mode JSON ``/check`` handed back. Showing
    the artist ``{"radial": 4}`` reads like an error code; what they need to know
    is that their part is fine and simply prints in pieces.
    """
    raw = str(getattr(props, "suggested_mode", "") or "").strip()
    if not raw:
        return ""
    try:
        mode = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    return "%s — press Segment" % split_phrase({"mode": mode})


def design_overall(props):
    """The verdict as the artist should read it: a printable split is not a fail.

    ``props.check_overall`` keeps the service's own word untouched. This is the
    panel's presentation of it — the worst status among the checks that actually
    constrain the DESIGN, which a bed_fit row we can simply cut up does not.
    """
    rank = {"pass": 0, "warn": 1, "fail": 2}
    worst = -1
    for row in props.checks:
        status = "pass" if row.split else str(row.status or "").lower()
        worst = max(worst, rank.get(status, 2))
    if worst < 0:
        return str(props.check_overall or "").lower()
    for name, value in rank.items():
        if value == worst:
            return name
    return str(props.check_overall or "").lower()


class ServiceError(Exception):
    """The geometry service refused or could not be reached."""


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _extract_error(body):
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body.strip()[:600]
    if isinstance(parsed, dict):
        message = str(parsed.get("error") or parsed.get("message") or "").strip()
        tb = parsed.get("traceback")
        if tb:
            message = (message + "\n" + str(tb)).strip()
        if message:
            return message[:2000]
    return str(parsed)[:600]


def request_json(url, payload=None, timeout=30.0, method=None):
    """POST/GET JSON. Raises :class:`ServiceError` with an actionable message."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method is None:
        method = "POST" if data is not None else "GET"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    # Never route loopback traffic through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise ServiceError(
            "Geometry service returned HTTP %s for %s:\n%s"
            % (exc.code, url, _extract_error(body) or exc.reason)
        )
    except urllib.error.URLError as exc:
        raise ServiceError(
            "Could not reach the geometry service at %s (%s). Is it running on port 8765?"
            % (url, getattr(exc, "reason", exc))
        )
    except OSError as exc:
        raise ServiceError("Request to %s failed after %.0fs: %s" % (url, timeout, exc))

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ServiceError("Geometry service response was not UTF-8: %s" % exc)
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ServiceError("Geometry service response was not JSON: %s\n%s" % (exc, text[:400]))
    if not isinstance(parsed, dict):
        raise ServiceError("Geometry service response was not a JSON object.")
    return parsed


# ---------------------------------------------------------------------------
# async plumbing (worker thread -> main thread via bpy.app.timers)
# ---------------------------------------------------------------------------

def run_async(work, done):
    """Run ``work()`` off the main thread, call ``done(value, error)`` on it.

    ``work`` must not touch ``bpy``; capture everything it needs beforehand.
    In background mode there is no timer loop worth waiting on, so it runs
    synchronously instead.
    """
    if bpy.app.background:
        try:
            done(work(), None)
        except Exception as exc:  # noqa: BLE001
            done(None, exc)
        return

    box = {"done": False, "value": None, "error": None}

    def worker():
        try:
            box["value"] = work()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            box["done"] = True

    threading.Thread(target=worker, name="ForgePartForge", daemon=True).start()

    def poll():
        if not box["done"]:
            return 0.1
        try:
            done(box["value"], box["error"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tag_redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.1)


def _tag_redraw():
    if bpy.app.background:
        return
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except (AttributeError, TypeError):
        pass


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

_SUPPRESS_UPDATE = False


def _clamp(value, low, high, has_low, has_high):
    if has_low and value < low:
        return low
    if has_high and value > high:
        return high
    return value


def _param_update(self, context):
    """Keep the edited value inside the schema's min/max.

    ``step`` is deliberately *not* snapped: schema defaults such as 152.4 with a
    step of 1.0 are legal and must survive editing (Blender treats step as a UI
    drag increment, not a constraint).
    """
    global _SUPPRESS_UPDATE
    if _SUPPRESS_UPDATE:
        return
    _SUPPRESS_UPDATE = True
    try:
        if self.kind == "FLOAT":
            value = _clamp(self.float_value, self.value_min, self.value_max, self.has_min, self.has_max)
            if value != self.float_value:
                self.float_value = value
        elif self.kind == "INT":
            value = int(
                _clamp(self.int_value, self.value_min, self.value_max, self.has_min, self.has_max)
            )
            if value != self.int_value:
                self.int_value = value
    finally:
        _SUPPRESS_UPDATE = False


class ForgeParam(PropertyGroup):
    """One entry of a script's PARAMS block, mirrored into the UI."""

    name: StringProperty(name="Key", description="Parameter name in the script's PARAMS block")
    label: StringProperty(name="Label")
    description: StringProperty(name="Description")
    unit: StringProperty(name="Unit", default="mm")
    kind: EnumProperty(
        name="Kind",
        items=(
            ("FLOAT", "Float", "Floating point value"),
            ("INT", "Integer", "Whole number"),
            ("BOOL", "Boolean", "On/off"),
        ),
        default="FLOAT",
    )
    float_value: FloatProperty(name="Value", precision=4, step=10, update=_param_update)
    int_value: IntProperty(name="Value", update=_param_update)
    bool_value: BoolProperty(name="Value", update=_param_update)
    value_min: FloatProperty(name="Min")
    value_max: FloatProperty(name="Max")
    value_step: FloatProperty(name="Step", default=1.0)
    has_min: BoolProperty(default=False)
    has_max: BoolProperty(default=False)
    has_step: BoolProperty(default=False)

    def get_value(self):
        if self.kind == "BOOL":
            return bool(self.bool_value)
        if self.kind == "INT":
            return int(self.int_value)
        return float(self.float_value)

    def set_value(self, value):
        global _SUPPRESS_UPDATE
        _SUPPRESS_UPDATE = True
        try:
            if self.kind == "BOOL":
                self.bool_value = bool(value)
            elif self.kind == "INT":
                self.int_value = int(round(float(value)))
            else:
                self.float_value = float(value)
        except (TypeError, ValueError):
            pass
        finally:
            _SUPPRESS_UPDATE = False

    def range_text(self):
        bits = []
        if self.has_min or self.has_max:
            low = ("%g" % self.value_min) if self.has_min else "-"
            high = ("%g" % self.value_max) if self.has_max else "-"
            bits.append("%s .. %s" % (low, high))
        if self.has_step:
            bits.append("step %g" % self.value_step)
        if self.unit and self.unit not in {"count", "bool"}:
            bits.append(self.unit)
        return "  ".join(bits)


class ForgeCheckResult(PropertyGroup):
    """One row of ``/check``'s ``checks`` list, flattened for the panel."""

    name: StringProperty(name="Check")
    status: StringProperty(name="Status", default="")
    details: StringProperty(name="Details", default="")
    hint: StringProperty(
        name="Hint",
        description="Follow-up the service suggested (e.g. the segmentation mode)",
        default="",
    )
    split: BoolProperty(
        name="Handled by splitting",
        description=(
            "This check only failed because the part is bigger than the bed, and "
            "the service can cut it into pieces that fit. Print planning, not a "
            "fault: the row is shown as a split, not a failure"
        ),
        default=False,
    )

    def icon(self):
        # A part that merely prints in pieces is not broken, so it never wears
        # the CANCEL icon: the design is fine, the plate is just smaller.
        if self.split:
            return "MOD_BOOLEAN"
        return CHECK_ICONS.get(self.status.lower(), "QUESTION")


class ForgePartForgeProps(PropertyGroup):
    """Scene-level PartForge project state."""

    script_path: StringProperty(
        name="Script",
        description="Build123d script with a PARAMS block at the top",
        subtype="FILE_PATH",
    )
    object_name: StringProperty(
        name="Object",
        description="Blender object the generated mesh is loaded into (replaced in place)",
        default="ForgePart",
    )
    collection_name: StringProperty(
        name="Collection",
        description="Collection new objects are linked into (blank = scene collection)",
        default="",
    )
    params: CollectionProperty(type=ForgeParam)
    params_json: StringProperty(name="Schema JSON", default="")
    status: StringProperty(name="Status", default="Idle")
    status_is_error: BoolProperty(default=False)
    busy: BoolProperty(default=False)
    stats: StringProperty(name="Stats", default="")
    show_descriptions: BoolProperty(
        name="Show Descriptions",
        description="Show each parameter's description and allowed range",
        default=False,
    )
    export_format: EnumProperty(name="Format", items=EXPORT_FORMATS, default="stl")
    export_path: StringProperty(
        name="Export Path",
        description="Output file. Blank = <script folder>/exports/<script name>.<format>",
        subtype="FILE_PATH",
    )

    # --- print readiness (Phase 2) -----------------------------------------
    checks: CollectionProperty(type=ForgeCheckResult)
    check_overall: StringProperty(
        name="Overall",
        description="Worst status of the last /check run: pass, warn or fail",
        default="",
    )
    check_summary: StringProperty(
        name="Check Summary",
        description="Printer profile and bounding box the last check ran against",
        default="",
    )
    suggested_mode: StringProperty(
        name="Suggested Mode",
        description="Segmentation mode /check suggested, as JSON",
        default="",
    )

    joint_type: EnumProperty(
        name="Joint",
        description="Joint cut into every mating face",
        items=JOINT_TYPES,
        default="dovetail",
    )
    joint_tolerance: FloatProperty(
        name="Tolerance",
        description=(
            "Gap across every mating face, mm. 0 = use the printer profile "
            "(press_fit for dovetail/pin, magnet_pocket_extra for magnets)"
        ),
        default=0.0,
        min=0.0,
        max=2.0,
        precision=3,
    )
    segment_mode: EnumProperty(
        name="Mode",
        description="How the part is cut up",
        items=SEGMENT_MODES,
        default="AUTO",
    )
    segment_radial: IntProperty(
        name="Wedges",
        description="Number of equal wedges about Z",
        default=4,
        min=2,
        max=64,
    )
    segment_planar: StringProperty(
        name="Z Heights",
        description="Comma-separated Z heights in mm to cut at, e.g. 30, 60",
        default="",
    )
    segment_collection: StringProperty(
        name="Segment Collection",
        description="Collection the loaded segments are linked into (blank = scene collection)",
        default="",
    )
    segment_summary: StringProperty(
        name="Segment Summary",
        description="Result of the last segment run",
        default="",
    )
    segment_export_dir: StringProperty(
        name="Segment Folder",
        description="Folder the segment files are written to. Blank = <script folder>/exports",
        subtype="DIR_PATH",
    )
    segment_basename: StringProperty(
        name="Basename",
        description="File-name stem for the segment files. Blank = the script's name",
        default="",
    )


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_partforge", None)


def set_status(props, message, error=False):
    if props is None:
        return
    props.status = str(message).strip().splitlines()[0][:400] if message else ""
    props.status_is_error = bool(error)
    if error and message:
        print("[Forge/PartForge]", message)


# ---------------------------------------------------------------------------
# schema <-> properties
# ---------------------------------------------------------------------------

def _pretty_label(key):
    return key.replace("_", " ").strip().title()


def sync_schema(props, schema, keep_values=False):
    """Rebuild the parameter collection from a PARAMS schema dict."""
    if not isinstance(schema, dict):
        raise ForgeError("The service returned a 'params' block that is not an object.")

    previous = {}
    if keep_values:
        for item in props.params:
            previous[item.name] = item.get_value()

    props.params.clear()
    for key, spec in schema.items():
        if not isinstance(spec, dict):
            spec = {"value": spec}
        item = props.params.add()
        item.name = str(key)
        item.label = str(spec.get("label") or _pretty_label(str(key)))
        item.description = str(spec.get("description") or "")
        unit = str(spec.get("unit") or "mm").strip().lower()
        item.unit = unit
        value = spec.get("value")
        kind = _UNIT_KINDS.get(unit)
        if kind is None:
            if isinstance(value, bool):
                kind = "BOOL"
            elif isinstance(value, int):
                kind = "INT"
            else:
                kind = "FLOAT"
        item.kind = kind

        low = spec.get("min")
        high = spec.get("max")
        step = spec.get("step")
        if isinstance(low, (int, float)) and not isinstance(low, bool):
            item.value_min = float(low)
            item.has_min = True
        if isinstance(high, (int, float)) and not isinstance(high, bool):
            item.value_max = float(high)
            item.has_max = True
        if isinstance(step, (int, float)) and not isinstance(step, bool) and float(step) > 0.0:
            item.value_step = float(step)
            item.has_step = True

        if keep_values and key in previous:
            item.set_value(previous[key])
        else:
            item.set_value(value if value is not None else 0)

    props.params_json = json.dumps(schema)


def overrides(props):
    """Current parameter values as a plain dict for the service."""
    return {item.name: item.get_value() for item in props.params}


def read_script(props):
    """Absolute script path + its source text."""
    path = props.script_path.strip() if props.script_path else ""
    if not path:
        raise ForgeError("Set a script path first (a .py file with a PARAMS block).")
    path = common.resolve_path(path)
    if not os.path.isfile(path):
        raise ForgeError("Script not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        raise ForgeError("Could not read %s: %s" % (path, exc))
    if not source.strip():
        raise ForgeError("Script %s is empty." % path)
    return path, source


def default_export_path(props):
    script_path = props.script_path.strip() if props.script_path else ""
    fmt = props.export_format
    if props.export_path.strip():
        return common.resolve_path(props.export_path, make_parents=True, default_ext="." + fmt)
    if not script_path:
        raise ForgeError("Set an export path (no script path to derive one from).")
    script_path = common.resolve_path(script_path)
    folder = os.path.join(os.path.dirname(script_path), "exports")
    stem = os.path.splitext(os.path.basename(script_path))[0] or "part"
    return common.resolve_path(os.path.join(folder, stem + "." + fmt), make_parents=True)


def load_printer():
    """``(printer_dict_or_None, source_label)`` from the add-on preference.

    A blank preference is not an error: the geometry service merges whatever it
    is given over its own Elegoo Centauri Carbon defaults, so sending nothing is
    a valid (and honest) way to say "the default printer".
    """
    raw = str(pref("printer_path") or "").strip()
    if not raw:
        return None, "service default profile"
    path = common.resolve_path(raw)
    if not os.path.isfile(path):
        raise ForgeError(
            "Printer profile not found: %s (set it in the Forge add-on preferences, "
            "or clear it to use the service default)" % path
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            profile = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ForgeError("Could not read the printer profile %s: %s" % (path, exc))
    if not isinstance(profile, dict):
        raise ForgeError("Printer profile %s must contain a JSON object." % path)
    return profile, os.path.basename(path)


def segment_mode(props):
    """The panel's mode widgets as the ``mode`` value ``/segment`` expects."""
    kind = props.segment_mode
    if kind == "AUTO":
        return "auto"
    if kind == "RADIAL":
        count = int(props.segment_radial)
        if count < 2:
            raise ForgeError("Radial mode needs at least 2 wedges.")
        return {"radial": count}

    heights = []
    for chunk in str(props.segment_planar or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            heights.append(float(chunk))
        except ValueError:
            raise ForgeError("'%s' is not a Z height in mm. Use e.g. 30, 60" % chunk)
    if not heights:
        raise ForgeError("Planar mode needs at least one Z height, e.g. 30, 60")
    return {"planar": heights}


def joint_spec(props):
    spec = {"type": props.joint_type}
    if props.joint_tolerance > 0.0:
        spec["tolerance"] = float(props.joint_tolerance)
    return spec


def segment_export_dir(props):
    """Where Export Segments writes: the property, or <script folder>/exports."""
    raw = str(props.segment_export_dir or "").strip()
    if raw:
        folder = common.resolve_path(raw)
    else:
        script_path = str(props.script_path or "").strip()
        if not script_path:
            raise ForgeError("Set a segment folder (no script path to derive one from).")
        folder = os.path.join(os.path.dirname(common.resolve_path(script_path)), "exports")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise ForgeError("Could not create %s: %s" % (folder, exc))
    return folder


def segment_basename(props):
    raw = str(props.segment_basename or "").strip()
    if raw:
        return raw
    script_path = str(props.script_path or "").strip()
    if script_path:
        stem = os.path.splitext(os.path.basename(common.resolve_path(script_path)))[0]
        if stem:
            return stem
    return "part"


def store_checks(props, payload):
    """Flatten ``/check``'s response onto the scene props for the panel."""
    props.checks.clear()
    props.check_overall = str(payload.get("overall") or "")
    props.suggested_mode = ""

    for entry in payload.get("checks") or []:
        if not isinstance(entry, dict):
            continue
        row = props.checks.add()
        row.name = str(entry.get("name") or "?")
        row.status = str(entry.get("status") or "").lower()
        row.details = str(entry.get("details") or "")
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        if row.name == "bed_fit":
            suggestion = data.get("suggested_segmentation")
            if isinstance(suggestion, dict) and suggestion.get("mode") is not None:
                props.suggested_mode = json.dumps(suggestion.get("mode"))
                if row.status == "fail":
                    # Bigger than the bed is print planning, not a design
                    # fault: say how it prints, not that it is broken.
                    row.split = True
                    row.hint = "%s — cut it in the Segments box below." % (
                        split_phrase(suggestion),)

    printer = payload.get("printer") if isinstance(payload.get("printer"), dict) else {}
    bed = printer.get("bed") if isinstance(printer.get("bed"), dict) else {}
    bits = []
    if printer.get("name"):
        bits.append(str(printer["name"]))
    if bed:
        try:
            bits.append("bed %g x %g x %g mm" % (bed.get("x"), bed.get("y"), bed.get("z")))
        except (TypeError, ValueError):
            pass
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    box = stats.get("bounding_box_mm")
    if isinstance(box, (list, tuple)) and len(box) == 3:
        try:
            bits.append("part %.1f x %.1f x %.1f mm" % tuple(float(v) for v in box))
        except (TypeError, ValueError):
            pass
    props.check_summary = "   ".join(bits)


def load_segment_objects(payload, collection=None):
    """Build one object per returned segment, laid out on the packed plate.

    Positions come from ``plate.items``: each entry says where that segment's
    rotated bounding-box minimum corner goes, so the viewport shows the print
    plate rather than the assembled part.  Same convention as the MCP
    ``partforge_load_segments`` tool, because it is the same wire data.
    """
    placements = {}
    plate = payload.get("plate") if isinstance(payload.get("plate"), dict) else {}
    for item in plate.get("items") or []:
        if isinstance(item, dict) and item.get("name"):
            placements[str(item["name"])] = item

    names = []
    with common.object_mode():
        for segment in payload.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            name = str(segment.get("name") or "segment")
            mesh = segment.get("mesh") if isinstance(segment.get("mesh"), dict) else {}
            vertices = mesh.get("vertices")
            if not vertices:
                continue
            obj, _info = common.build_mesh_object(
                name,
                vertices,
                mesh.get("faces") or [],
                replace=True,
                collection=collection or None,
                scale=common.MM_TO_M,
            )
            if name in placements:
                common.apply_plate_placement(obj, placements[name], scale=common.MM_TO_M)
            names.append(obj.name)
    return names


def format_segment_summary(payload, loaded_names=None):
    segments = [s for s in (payload.get("segments") or []) if isinstance(s, dict)]
    pieces = sum(1 for s in segments if s.get("kind") != "hardware")
    hardware = len(segments) - pieces
    mode = payload.get("mode") if isinstance(payload.get("mode"), dict) else {}
    kind = mode.get("kind", "?")
    if kind == "radial":
        mode_text = "radial x%s" % mode.get("count", "?")
    elif kind == "planar":
        mode_text = "planar x%d" % (len(mode.get("heights") or []) + 1)
    else:
        mode_text = str(kind)

    bits = ["%d segment(s)" % pieces]
    if hardware:
        bits.append("%d pin(s)" % hardware)
    bits.append(mode_text)
    joint = payload.get("joint") if isinstance(payload.get("joint"), dict) else {}
    if joint.get("type"):
        bits.append(str(joint["type"]))
    plate = payload.get("plate") if isinstance(payload.get("plate"), dict) else {}
    if plate:
        bits.append("plate %s" % ("fits" if plate.get("fits") else "DOES NOT FIT"))
    if loaded_names is not None:
        bits.append("%d loaded" % len(loaded_names))
    return "   ".join(bits)


def _timeout(name, fallback):
    value = getattr(get_prefs(), name, fallback)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = fallback
    return max(1.0, value)


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _alive(props):
    """True while the PropertyGroup pointer is still valid (file reload safety)."""
    try:
        return props is not None and props.busy in (True, False)
    except (ReferenceError, AttributeError):
        return False


class _PartForgeOperator(Operator):
    # No UNDO: the data change lands later, from the timer callback.
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy


class FORGE_OT_pf_health(_PartForgeOperator):
    bl_idname = "forge.pf_health"
    bl_label = "Check Service"
    bl_description = "Ping the geometry service and report its Build123d version"

    def execute(self, context):
        props = get_props(context)
        url = service_url("/health")
        timeout = _timeout("request_timeout", 120.0)
        props.busy = True
        set_status(props, "Checking %s ..." % url)

        def work():
            return request_json(url, payload=None, timeout=min(15.0, timeout), method="GET")

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            version = value.get("build123d") or "unknown"
            set_status(props, "Service OK (build123d %s)" % version)

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_load_script(_PartForgeOperator):
    bl_idname = "forge.pf_load_script"
    bl_label = "Load Script"
    bl_description = "Read the script's PARAMS block through the geometry service and build the panel"

    keep_values: BoolProperty(
        name="Keep Current Values",
        description="Keep values already tuned in the panel for parameters that still exist",
        default=False,
    )

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        url = service_url("/parse_params")
        timeout = min(60.0, _timeout("request_timeout", 120.0))
        keep = self.keep_values
        props.busy = True
        set_status(props, "Reading parameters from %s ..." % os.path.basename(path))

        def work():
            return request_json(url, {"script": source}, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            try:
                sync_schema(props, value.get("params") or {}, keep_values=keep)
            except ForgeError as exc:
                set_status(props, str(exc), error=True)
                return
            if not props.object_name.strip():
                props.object_name = os.path.splitext(os.path.basename(path))[0].title() or "ForgePart"
            set_status(props, "Loaded %d parameter(s) from %s" % (len(props.params), os.path.basename(path)))

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_regenerate(_PartForgeOperator):
    bl_idname = "forge.pf_regenerate"
    bl_label = "Regenerate"
    bl_description = "Rebuild the part from the current parameter values and reload it in place"

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        payload = {"script": source, "overrides": overrides(props)}
        url = service_url("/generate")
        timeout = _timeout("request_timeout", 120.0)
        object_name = props.object_name.strip() or os.path.splitext(os.path.basename(path))[0] or "ForgePart"
        collection = props.collection_name.strip()
        props.busy = True
        set_status(props, "Generating %s ..." % object_name)

        def work():
            return request_json(url, payload, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            mesh = value.get("mesh") or {}
            vertices = mesh.get("vertices")
            faces = mesh.get("faces")
            if vertices is None:
                set_status(props, "Service response contained no mesh vertices.", error=True)
                return
            try:
                with common.object_mode():
                    obj, info = common.build_mesh_object(
                        object_name,
                        vertices,
                        faces or [],
                        replace=True,
                        collection=collection or None,
                        scale=common.MM_TO_M,
                    )
            except ForgeError as exc:
                set_status(props, str(exc), error=True)
                return
            except Exception as exc:  # noqa: BLE001
                set_status(props, "Failed to build mesh: %s" % exc, error=True)
                traceback.print_exc()
                return

            props.object_name = obj.name
            schema = value.get("params")
            if isinstance(schema, dict) and schema:
                try:
                    sync_schema(props, schema, keep_values=False)
                except ForgeError:
                    pass
            props.stats = _format_stats(value.get("stats") or {}, info)
            set_status(props, "Generated %s (%d verts, %d faces)"
                       % (obj.name, info["vertex_count"], info["face_count"]))

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_export(_PartForgeOperator):
    bl_idname = "forge.pf_export"
    bl_label = "Export"
    bl_description = "Ask the geometry service to write STL / STEP / 3MF from the current parameters"

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
            output = default_export_path(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        payload = {
            "script": source,
            "overrides": overrides(props),
            "format": props.export_format,
            "path": output,
        }
        url = service_url("/export")
        timeout = max(300.0, _timeout("request_timeout", 120.0))
        props.busy = True
        set_status(props, "Exporting %s ..." % os.path.basename(output))

        def work():
            return request_json(url, payload, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            written = value.get("path") or output
            set_status(props, "Exported %s" % written)

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_reset_params(_PartForgeOperator):
    bl_idname = "forge.pf_reset_params"
    bl_label = "Reset Values"
    bl_description = "Restore every parameter to the value stored in the last loaded schema"

    def execute(self, context):
        props = get_props(context)
        if not props.params_json:
            self.report({"WARNING"}, "No schema loaded yet")
            return {"CANCELLED"}
        try:
            schema = json.loads(props.params_json)
        except ValueError as exc:
            set_status(props, "Stored schema is corrupt: %s" % exc, error=True)
            return {"CANCELLED"}
        try:
            sync_schema(props, schema, keep_values=False)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}
        set_status(props, "Parameters reset to schema defaults")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# print readiness operators (Phase 2)
# ---------------------------------------------------------------------------

class FORGE_OT_pf_check(_PartForgeOperator):
    bl_idname = "forge.pf_check"
    bl_label = "Run Checks"
    bl_description = (
        "Ask the geometry service whether this part can be printed: bed fit, "
        "wall thickness, overhangs and watertightness"
    )

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
            printer, printer_source = load_printer()
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        payload = {"script": source, "overrides": overrides(props)}
        if printer:
            payload["printer"] = printer
        url = service_url("/check")
        # The service allows itself 120 s for a check; outlive that, don't race it.
        timeout = max(150.0, _timeout("request_timeout", 120.0))
        props.busy = True
        set_status(props, "Checking %s against %s ..."
                   % (os.path.basename(path), printer_source))

        def work():
            return request_json(url, payload, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            try:
                store_checks(props, value)
            except Exception as exc:  # noqa: BLE001
                set_status(props, "Could not read the check response: %s" % exc, error=True)
                traceback.print_exc()
                return
            # The stored check_overall keeps the service's word. The status LINE
            # discounts a bed_fit row that only needs cutting up, so a part that
            # is simply bigger than the plate never flashes red at the artist.
            overall = design_overall(props) or "?"
            split = any(row.split for row in props.checks)
            line = "Print checks: %s (%d check(s))" % (overall.upper(), len(props.checks))
            if split:
                line += " — prints in pieces, cut it in Segments"
            set_status(props, line, error=(overall == "fail"))

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_segment(_PartForgeOperator):
    bl_idname = "forge.pf_segment"
    bl_label = "Segment"
    bl_description = (
        "Cut the part into printable segments with mating joints and lay the "
        "pieces out in the viewport the way they will sit on the plate"
    )

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
            printer, printer_source = load_printer()
            mode = segment_mode(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        payload = {
            "script": source,
            "overrides": overrides(props),
            "mode": mode,
            "joint": joint_spec(props),
            "include_mesh": True,
        }
        if printer:
            payload["printer"] = printer
        url = service_url("/segment")
        # Segmenting is dozens of OCC booleans; the service allows itself 300 s.
        timeout = max(330.0, _timeout("request_timeout", 120.0))
        collection = props.segment_collection.strip()
        props.busy = True
        set_status(props, "Segmenting %s (%s) ..." % (os.path.basename(path), printer_source))

        def work():
            return request_json(url, payload, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            try:
                names = load_segment_objects(value, collection=collection or None)
            except ForgeError as exc:
                set_status(props, str(exc), error=True)
                return
            except Exception as exc:  # noqa: BLE001
                set_status(props, "Failed to build the segments: %s" % exc, error=True)
                traceback.print_exc()
                return
            props.segment_summary = format_segment_summary(value, names)
            if not names:
                set_status(props, "The service returned no segment meshes.", error=True)
                return
            set_status(props, "Loaded %d segment(s): %s"
                       % (len(names), ", ".join(names[:6]) + (" ..." if len(names) > 6 else "")))

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_pf_export_segments(_PartForgeOperator):
    bl_idname = "forge.pf_export_segments"
    bl_label = "Export Segments"
    bl_description = (
        "Write one file per segment (oriented, centred, on Z=0) plus a packed "
        "plate 3MF, straight from the geometry service"
    )

    def execute(self, context):
        props = get_props(context)
        try:
            path, source = read_script(props)
            printer, printer_source = load_printer()
            mode = segment_mode(props)
            directory = segment_export_dir(props)
            basename = segment_basename(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        payload = {
            "script": source,
            "overrides": overrides(props),
            "mode": mode,
            "joint": joint_spec(props),
            "include_mesh": False,
            "directory": directory,
            "basename": basename,
            "format": props.export_format,
        }
        if printer:
            payload["printer"] = printer
        url = service_url("/export_segments")
        timeout = max(330.0, _timeout("request_timeout", 120.0))
        props.busy = True
        set_status(props, "Exporting segments of %s (%s) to %s ..."
                   % (os.path.basename(path), printer_source, directory))

        def work():
            return request_json(url, payload, timeout=timeout)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            files = value.get("files") or []
            props.segment_summary = "%d file(s) in %s" % (
                len(files) + (1 if value.get("plate_path") else 0),
                value.get("directory") or directory,
            )
            set_status(props, "Wrote %d segment file(s) + plate to %s"
                       % (len(files), value.get("directory") or directory))

        run_async(work, done)
        return {"FINISHED"}


def _format_stats(stats, info):
    bits = []
    vertex_count = stats.get("vertex_count", info.get("vertex_count"))
    face_count = stats.get("face_count", info.get("face_count"))
    if vertex_count is not None and face_count is not None:
        bits.append("%s verts / %s faces" % (vertex_count, face_count))
    box = stats.get("bounding_box_mm")
    if isinstance(box, (list, tuple)) and len(box) == 3:
        try:
            bits.append("%.1f x %.1f x %.1f mm" % tuple(float(v) for v in box))
        except (TypeError, ValueError):
            pass
    if "watertight" in stats:
        bits.append("watertight: %s" % ("yes" if stats.get("watertight") else "NO"))
    return "   ".join(bits)


# ---------------------------------------------------------------------------
# socket command (additive): partforge_open
# ---------------------------------------------------------------------------

#: Script filenames that say nothing about the part -- the folder name does.
#: The same list the MCP server keeps (``mcp/forge_mcp/util.py``), so the panel
#: and the tools agree on what the generated object is called: a panel pointed
#: at ``projects/small-magnet-holder/part.py`` regenerates the very object
#: ``partforge_generate`` made, rather than a second one called "Part".
_GENERIC_STEMS = ("part", "main", "model", "script", "build", "generate", "__init__")

#: Blender caps object names at 63 bytes.
_MAX_OBJECT_NAME = 63


def object_name_for_script(path):
    """``projects/small-magnet-holder/part.py`` -> ``small-magnet-holder``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.lower() in _GENERIC_STEMS:
        folder = os.path.basename(os.path.dirname(path))
        if folder:
            stem = folder
    stem = stem.strip() or "ForgePart"
    encoded = stem.encode("utf-8")[:_MAX_OBJECT_NAME]
    return encoded.decode("utf-8", "ignore") or "ForgePart"


def clear_results(props):
    """Forget the previous script's results when the panel changes part.

    A FAIL row left over from another part is worse than no row at all: the
    artist reads the panel, not the chat log.
    """
    props.checks.clear()
    props.check_overall = ""
    props.check_summary = ""
    props.suggested_mode = ""
    props.segment_summary = ""
    props.stats = ""


def _resolved_or_blank(raw):
    try:
        text = str(raw or "").strip()
        return common.resolve_path(text) if text else ""
    except Exception:  # noqa: BLE001 - a junk stored path is simply "no path"
        return ""


@command("partforge_open")
def cmd_partforge_open(params):
    """Point the PartForge panel at a script and build its sliders.

    Additive command behind the MCP tool ``partforge_open_in_panel``: it does
    what the panel's own **Load Script** button does (set the path, ask the
    geometry service for the PARAMS schema, rebuild the parameter collection)
    without the artist having to type a path into a file field.  Same plumbing,
    no fork -- ``read_script`` / ``request_json`` / ``sync_schema``.

    params: ``script_path`` (required), ``keep_values?`` (keep values already
    tuned for parameters that still exist), ``object?`` (override the object
    name the panel will build into), ``params?`` (a schema supplied directly,
    which skips the service call -- used by the headless tests).

    Runs on the main thread like every command, so the ``/parse_params`` request
    is synchronous here rather than going through ``run_async``; parsing builds
    no geometry, so it is a short call.
    """
    props = get_props()
    if props is None:
        raise ForgeError(
            "This scene has no PartForge properties. Enable the Forge add-on "
            "(Edit > Preferences > Add-ons > Forge) and try again."
        )

    raw = params.get("script_path") or params.get("script") or params.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ForgeError(
            "partforge_open needs 'script_path': the .py file with the PARAMS "
            "block, e.g. projects/small-magnet-holder/part.py"
        )
    path = common.resolve_path(raw)
    if not os.path.isfile(path):
        raise ForgeError("Script not found: %s" % path)

    schema = params.get("params")
    if schema is not None and not isinstance(schema, dict):
        raise ForgeError("'params', when given, must be a PARAMS schema object.")
    keep_values = bool(params.get("keep_values", False))

    override_name = params.get("object")
    if override_name is not None and not isinstance(override_name, str):
        raise ForgeError("'object' must be the object name to build into, as a string.")

    previous_path = _resolved_or_blank(props.script_path)
    props.script_path = path
    try:
        if schema is None:
            _path, source = read_script(props)
            url = service_url("/parse_params")
            timeout = min(60.0, _timeout("request_timeout", 120.0))
            value = request_json(url, {"script": source}, timeout=timeout)
            schema = value.get("params") or {}
            source_label = "service"
        else:
            source_label = "supplied"
        sync_schema(props, schema, keep_values=keep_values)
    except ServiceError as exc:
        props.script_path = previous_path  # leave the panel as we found it
        raise ForgeError(str(exc))
    except ForgeError:
        props.script_path = previous_path
        raise

    changed = previous_path != path
    if override_name and override_name.strip():
        props.object_name = override_name.strip()
    elif changed or not props.object_name.strip():
        props.object_name = object_name_for_script(path)
    if changed:
        clear_results(props)

    names = [item.name for item in props.params]
    set_status(props, "Loaded %d parameter(s) from %s"
               % (len(names), os.path.basename(path)))
    _tag_redraw()
    return {
        "script": path,
        "param_count": len(names),
        "params": names,
        "object": props.object_name,
        "schema_source": source_label,
    }


_CLASSES = (
    ForgeParam,
    ForgeCheckResult,
    ForgePartForgeProps,
    FORGE_OT_pf_health,
    FORGE_OT_pf_load_script,
    FORGE_OT_pf_regenerate,
    FORGE_OT_pf_export,
    FORGE_OT_pf_reset_params,
    FORGE_OT_pf_check,
    FORGE_OT_pf_segment,
    FORGE_OT_pf_export_segments,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_partforge = bpy.props.PointerProperty(type=ForgePartForgeProps)


def unregister():
    try:
        del bpy.types.Scene.forge_partforge
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
