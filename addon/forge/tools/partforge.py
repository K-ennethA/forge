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

from ..prefs import get_prefs, service_url
from . import common
from .registry import ForgeError

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


_CLASSES = (
    ForgeParam,
    ForgePartForgeProps,
    FORGE_OT_pf_health,
    FORGE_OT_pf_load_script,
    FORGE_OT_pf_regenerate,
    FORGE_OT_pf_export,
    FORGE_OT_pf_reset_params,
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
