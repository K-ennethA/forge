"""Flows: saved sequences of Forge operations that replay without an AI.

A flow is a small JSON document in ``<repo>/flows/*.json`` describing a linear
list of steps — Blender socket commands and geometry-service endpoints — with
``{{param}}`` placeholders in their arguments::

    {"name": "segment-into-4",
     "description": "...",
     "params": {"wedges": {"value": 4, "unit": "count"}},
     "steps": [{"kind": "service", "op": "/segment", "label": "...",
                "args": {"mode": {"radial": "{{wedges}}"}}},
               {"kind": "blender", "op": "load_meshes",
                "args": {"meshes": "{{steps.0.result.segments}}"}}]}

Why this exists: the assistant is wonderful at working out *how* to do something
the first time and a waste of a turn (and of money, and of determinism) the
tenth.  Once a job is understood it becomes a flow, and after that it is a
button in the sidebar that does exactly the same thing every time, with no model
in the loop.

Two conveniences the format leans on, both documented in ``flows/README.md``:

* a **service** step's ``script_path`` / ``printer_path`` arguments are files the
  runner reads for you — they become the ``script`` source and the ``printer``
  object in the request body, so flow JSON never carries a copy of a part.  Left
  blank they fall back to the PartForge panel's current script and the add-on's
  printer preference, which is what makes "segment *this* part" a saveable flow;
* a step argument may reference an earlier step's result with the dotted path
  ``{{steps.0.result.segments}}``.  Lookup only — no expressions, no arithmetic.

Everything is linear and fail-fast: the first step that errors stops the flow and
the error names the step, because "step 2 (Lay the pieces out on the plate)
failed" is actionable and "flow failed" is not.
"""

import json
import os
import queue
import re
import threading
import time
import traceback

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..prefs import pref, service_url
from . import registry
from .partforge import ServiceError, _tag_redraw
from .partforge import request_json as service_request
from .registry import ForgeError, command

FLOW_SUFFIX = ".json"

#: Geometry-service endpoints a flow step may call, with the seconds each is
#: allowed to take.  The budgets mirror the service's own limits
#: (docs/architecture.md: /check 120 s, /segment and /export_segments 300 s) plus
#: a little headroom, so a slow-but-working job is never turned into a mystery.
SERVICE_OPS = {
    "/health": 10.0,
    "/parse_params": 60.0,
    "/generate": 180.0,
    "/export": 300.0,
    "/check": 150.0,
    "/check_mesh": 150.0,
    "/segment": 330.0,
    "/segment_mesh": 330.0,
    "/export_segments": 330.0,
    "/slice": 600.0,
    "/mold": 330.0,
    "/export_mold": 330.0,
}

#: Arguments naming a file the runner reads, and the request field they become.
FILE_ARGS = {"script_path": "script", "printer_path": "printer"}

#: ``{{ anything }}``, with the surrounding whitespace allowed.
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

#: How long one Blender step may wait for the main thread when a flow is run
#: from the panel (the socket path is already on the main thread).
MAIN_THREAD_TIMEOUT = 600.0

BRIEF_LIMIT = 120


# ---------------------------------------------------------------------------
# where flows live
# ---------------------------------------------------------------------------

def flows_dir():
    """The folder flows are read from: the ``forge_flows_dir`` preference."""
    raw = str(pref("forge_flows_dir") or "").strip()
    if not raw:
        return ""
    try:
        return os.path.abspath(bpy.path.abspath(raw))
    except Exception:  # noqa: BLE001 - a junk stored path is simply "no path"
        return os.path.abspath(raw)


def _require_dir():
    directory = flows_dir()
    if not directory:
        raise ForgeError(
            "No flows folder is set. Edit > Preferences > Add-ons > Forge > "
            "Flows, and point 'Flows Folder' at the repo's flows/ directory."
        )
    if not os.path.isdir(directory):
        raise ForgeError(
            "The flows folder %s does not exist. Create it, or point the Flows "
            "Folder preference somewhere else." % directory
        )
    return directory


def flow_path(name):
    """``<flows dir>/<name>.json``, refusing anything that is not a bare name."""
    raw = str(name or "").strip().strip('"').strip()
    if not raw:
        raise ForgeError("No flow name given. Call flow_list to see what there is.")
    if raw.lower().endswith(FLOW_SUFFIX):
        raw = raw[: -len(FLOW_SUFFIX)]
    for marker in ("/", "\\", "..", ":", "\x00"):
        if marker in raw:
            raise ForgeError(
                "%r is not a flow name — it looks like a path. Flows are always "
                "read from the flows folder, so pass just the name, e.g. "
                "'segment-into-4'." % (name,)
            )
    return os.path.join(_require_dir(), raw + FLOW_SUFFIX)


def read_flow(name):
    """Load and validate one flow by name."""
    path = flow_path(name)
    if not os.path.isfile(path):
        known = [entry["name"] for entry in list_flows() if entry.get("name")]
        raise ForgeError(
            "No flow called %r in %s.%s"
            % (name, flows_dir(),
               (" There is: " + ", ".join(known)) if known else " That folder is empty.")
        )
    return validate_flow(_read_json(path), source=path)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except ValueError as exc:
        raise ForgeError("%s is not valid JSON: %s" % (os.path.basename(path), exc))
    except OSError as exc:
        raise ForgeError("Could not read %s: %s" % (path, exc))
    if not isinstance(value, dict):
        raise ForgeError("%s must contain a JSON object." % os.path.basename(path))
    return value


def list_flows():
    """Every flow in the folder, newest problems included rather than hidden."""
    directory = _require_dir()
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise ForgeError("Could not list %s: %s" % (directory, exc))

    for filename in names:
        if not filename.lower().endswith(FLOW_SUFFIX):
            continue
        path = os.path.join(directory, filename)
        stem = filename[: -len(FLOW_SUFFIX)]
        try:
            doc = validate_flow(_read_json(path), source=path)
        except ForgeError as exc:
            # A broken flow is reported, not swallowed: a flow that silently
            # vanishes from the list is a bug report nobody can write.
            out.append({"name": stem, "path": path, "error": str(exc)})
            continue
        out.append({
            "name": doc.get("name") or stem,
            "description": doc.get("description", ""),
            "params": doc.get("params", {}),
            "steps": len(doc.get("steps") or []),
            "step_labels": [step.get("label") or step.get("op", "")
                            for step in doc.get("steps") or []],
            "path": path,
        })
    return out


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_flow(doc, source=""):
    """Check a flow document's shape. Returns it; raises :class:`ForgeError`."""
    where = (" in %s" % os.path.basename(source)) if source else ""
    if not isinstance(doc, dict):
        raise ForgeError("A flow%s must be a JSON object." % where)

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ForgeError("The flow%s has no 'name'." % where)

    params = doc.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ForgeError("'params'%s must be an object of {name: {\"value\": ...}}." % where)
    for key, spec in params.items():
        if not isinstance(spec, dict) or "value" not in spec:
            raise ForgeError(
                "Parameter %r%s must be an object with a 'value' (and optionally "
                "'unit' and 'description')." % (key, where))

    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ForgeError("The flow%s has no 'steps'; a flow with no steps does nothing."
                         % where)
    for index, step in enumerate(steps):
        _validate_step(step, index, where)
    return doc


def _validate_step(step, index, where=""):
    label = "step %d%s" % (index + 1, where)
    if not isinstance(step, dict):
        raise ForgeError("%s must be an object." % label)
    kind = step.get("kind")
    if kind not in ("blender", "service"):
        raise ForgeError(
            "%s has kind %r; it must be \"blender\" (a Forge socket command) or "
            "\"service\" (a geometry-service endpoint)." % (label, kind))
    op = step.get("op")
    if not isinstance(op, str) or not op.strip():
        raise ForgeError("%s has no 'op'." % label)
    if kind == "blender":
        if op.strip() in ("flow_run", "flow_list"):
            raise ForgeError(
                "%s calls %r. Flows do not nest — write the steps out in this "
                "flow instead, so what it does is readable in one file." % (label, op))
        if not registry.has_command(op.strip()):
            raise ForgeError(
                "%s calls the Blender command %r, which does not exist. Known "
                "commands: %s" % (label, op, ", ".join(registry.command_names())))
    else:
        if normalize_endpoint(op) not in SERVICE_OPS:
            raise ForgeError(
                "%s calls the geometry-service endpoint %r, which this add-on "
                "does not know. Known endpoints: %s"
                % (label, op, ", ".join(sorted(SERVICE_OPS))))
    args = step.get("args", {})
    if args is not None and not isinstance(args, dict):
        raise ForgeError("%s: 'args' must be an object." % label)
    return step


def normalize_endpoint(op):
    text = str(op or "").strip()
    if not text:
        return ""
    return text if text.startswith("/") else "/" + text


def step_label(step, index):
    label = str(step.get("label") or "").strip()
    return label or ("%s %s" % (step.get("kind", "?"), step.get("op", "?")))


# ---------------------------------------------------------------------------
# parameters and templating
# ---------------------------------------------------------------------------

def resolve_params(doc, overrides):
    """Declared defaults with the caller's overrides on top, typed like the default."""
    declared = doc.get("params") or {}
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ForgeError("'params' must be an object of {name: value}.")

    unknown = [key for key in overrides if key not in declared]
    if unknown:
        raise ForgeError(
            "The flow %r has no parameter(s) %s. It takes: %s."
            % (doc.get("name"), ", ".join(sorted(unknown)),
               ", ".join(sorted(declared)) or "(none)"))

    out = {}
    for key, spec in declared.items():
        value = spec.get("value")
        if key in overrides:
            value = _coerce(overrides[key], spec.get("value"), key)
        out[key] = value
    return out


def _coerce(given, template, key):
    """Type the override like the declared default, so panel strings still work."""
    if isinstance(template, bool):
        if isinstance(given, str):
            return given.strip().lower() in ("1", "true", "yes", "on")
        return bool(given)
    if isinstance(template, int) and not isinstance(template, bool):
        try:
            return int(round(float(given)))
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r wants a whole number, got %r." % (key, given))
    if isinstance(template, float):
        try:
            return float(given)
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r wants a number, got %r." % (key, given))
    if isinstance(template, str):
        return "" if given is None else str(given)
    return given


def _lookup(path, params, results):
    """Resolve one ``{{...}}`` name: a parameter, or ``steps.N.result.a.b``."""
    parts = [part for part in str(path).split(".") if part != ""]
    if not parts:
        raise ForgeError("Empty {{}} placeholder.")

    if parts[0] == "steps":
        if len(parts) < 3 or parts[2] != "result":
            raise ForgeError(
                "%r is not a step reference. The only shape is "
                "{{steps.<n>.result.<field>.<field>}}." % path)
        try:
            index = int(parts[1])
        except ValueError:
            raise ForgeError("%r: the step number must be a whole number." % path)
        if index < 0 or index >= len(results):
            raise ForgeError(
                "%r refers to step %d, which has not run yet. A step can only "
                "read the results of steps before it." % (path, index))
        value = results[index]
        for part in parts[3:]:
            if isinstance(value, dict):
                if part not in value:
                    raise ForgeError(
                        "%r: step %d's result has no %r (it has: %s)."
                        % (path, index, part, ", ".join(sorted(value)) or "nothing"))
                value = value[part]
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (ValueError, IndexError):
                    raise ForgeError("%r: %r is not an index into that list." % (path, part))
            else:
                raise ForgeError("%r: %r is not a field of %s."
                                 % (path, part, type(value).__name__))
        return value

    if len(parts) == 1 and parts[0] in params:
        return params[parts[0]]
    raise ForgeError(
        "Unknown placeholder {{%s}}. This flow's parameters are: %s."
        % (path, ", ".join(sorted(params)) or "(none)"))


def substitute(value, params, results):
    """Fill ``{{...}}`` placeholders in a step's arguments.

    A string that is *only* a placeholder is replaced by the raw typed value —
    ``"{{wedges}}"`` becomes the number ``4``, not the string ``"4"``.  A
    placeholder embedded in a longer string is a text substitution.
    """
    if isinstance(value, dict):
        return dict((key, substitute(item, params, results)) for key, item in value.items())
    if isinstance(value, list):
        return [substitute(item, params, results) for item in value]
    if not isinstance(value, str):
        return value

    whole = _PLACEHOLDER.fullmatch(value.strip())
    if whole is not None:
        return _lookup(whole.group(1), params, results)

    def replace(match):
        found = _lookup(match.group(1), params, results)
        if isinstance(found, (dict, list)):
            return json.dumps(found)
        if isinstance(found, bool):
            return "true" if found else "false"
        return "" if found is None else str(found)

    return _PLACEHOLDER.sub(replace, value)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def _panel_script_path():
    scene = getattr(bpy.context, "scene", None)
    partforge = getattr(scene, "forge_partforge", None)
    raw = str(getattr(partforge, "script_path", "") or "").strip()
    return os.path.abspath(bpy.path.abspath(raw)) if raw else ""


def _read_text(path, what):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise ForgeError("Could not read the %s %s: %s" % (what, path, exc))


def prepare_service_body(op, args):
    """Turn a service step's args into the HTTP body, reading the file arguments."""
    body = dict(args or {})
    for key, field in FILE_ARGS.items():
        if key not in body:
            continue
        raw = str(body.pop(key) or "").strip()
        if not raw:
            # Blank = "whatever the panel is pointed at", which is what lets one
            # saved flow serve every part.
            raw = _panel_script_path() if key == "script_path" else str(pref("printer_path") or "")
            raw = str(raw or "").strip()
        if not raw:
            if key == "printer_path":
                continue  # no profile at all: the service uses its own default
            raise ForgeError(
                "This flow needs a part script. Either pass script_path, or point "
                "the PartForge panel at one (Forge tab > PartForge > Load Script) "
                "and run it again.")
        path = os.path.abspath(bpy.path.abspath(raw))
        if not os.path.isfile(path):
            raise ForgeError("No file at %s (from %s)." % (path, key))
        text = _read_text(path, "part script" if field == "script" else "printer profile")
        if field == "printer":
            try:
                body[field] = json.loads(text)
            except ValueError as exc:
                raise ForgeError("%s is not valid JSON: %s" % (path, exc))
        else:
            body[field] = text
    return body


def service_timeout(endpoint, args):
    """Seconds this endpoint may take: the step's ``timeout_s``, or the budget."""
    override = (args or {}).get("timeout_s")
    if isinstance(override, (int, float)) and override > 0:
        return float(override)
    return SERVICE_OPS.get(endpoint, 120.0)


def call_service(op, args):
    endpoint = normalize_endpoint(op)
    timeout = service_timeout(endpoint, args)
    body = prepare_service_body(endpoint, args)
    body.pop("timeout_s", None)
    url = service_url(endpoint)
    try:
        if endpoint == "/health" and not body:
            return service_request(url, None, timeout=timeout, method="GET")
        return service_request(url, body, timeout=timeout)
    except ServiceError as exc:
        raise ForgeError(str(exc))


def call_blender(op, args):
    status, result, message = registry.dispatch(str(op).strip(), args or {})
    if status != "success":
        raise ForgeError(message or "the command failed")
    return result if isinstance(result, dict) else {}


def run_flow(doc, overrides=None, blender=None, service=None):
    """Run a validated flow. Returns the report; raises on the first failure."""
    validate_flow(doc)
    blender = blender or call_blender
    service = service or call_service
    params = resolve_params(doc, overrides)

    started = time.time()
    steps = doc.get("steps") or []
    results = []
    reports = []

    for index, step in enumerate(steps):
        label = step_label(step, index)
        kind = step.get("kind")
        op = str(step.get("op") or "").strip()
        try:
            args = substitute(step.get("args") or {}, params, results)
            if kind == "blender":
                if isinstance(args, dict):
                    args.pop("timeout_s", None)
                result = blender(op, args)
            else:
                result = service(op, args)
        except ForgeError as exc:
            raise ForgeError(
                "Flow %r failed at step %d of %d (%s): %s%s"
                % (doc.get("name"), index + 1, len(steps), label, exc,
                   _done_so_far(reports)))
        except Exception as exc:  # noqa: BLE001 - a bug in a step is still that step's
            raise ForgeError(
                "Flow %r failed at step %d of %d (%s): %s: %s\n%s"
                % (doc.get("name"), index + 1, len(steps), label,
                   type(exc).__name__, exc, traceback.format_exc()))

        results.append(result if isinstance(result, dict) else {"value": result})
        reports.append({"index": index, "kind": kind, "op": op, "label": label,
                        "ok": True, "brief": brief(results[-1])})

    return {
        "flow": doc.get("name"),
        "description": doc.get("description", ""),
        "params": params,
        "steps": reports,
        "count": len(reports),
        "ok": True,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _done_so_far(reports):
    if not reports:
        return "  (nothing had run yet)"
    return "  (done first: %s)" % "; ".join(
        "%d %s" % (entry["index"] + 1, entry["label"]) for entry in reports)


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------

#: Result fields worth putting in a one-line summary, most useful first.
_BRIEF_KEYS = ("overall", "object", "objects", "names", "count", "segments",
               "params", "param_count", "path", "files", "mode", "vertex_count",
               "face_count", "actions", "tags", "islands", "removed", "pong")


def _short(value, limit=48):
    if isinstance(value, str):
        return _clip(value, limit)
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return ("%g" % value) if isinstance(value, float) else str(value)
    if isinstance(value, list):
        if not value:
            return "none"
        names = [item.get("name") for item in value
                 if isinstance(item, dict) and isinstance(item.get("name"), str)]
        if len(names) == len(value):
            return _clip("%d (%s)" % (len(value), ", ".join(names)), limit)
        if all(isinstance(item, str) for item in value):
            return _clip("%d (%s)" % (len(value), ", ".join(value)), limit)
        return "%d items" % len(value)
    if isinstance(value, dict):
        try:
            text = json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            text = ""
        if text and len(text) <= limit:
            return text
        # Too big to show: name what is in it, which for a schema or a result
        # map is the useful half anyway ("params=3 (width, depth, wall)").
        keys = [str(key) for key in value]
        return _clip("%d (%s)" % (len(keys), ", ".join(keys)), limit)
    return _clip(str(value), limit)


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def brief(result):
    """One readable line about what a step produced."""
    if not isinstance(result, dict) or not result:
        return "ok"
    parts = []
    for key in _BRIEF_KEYS:
        if key in result and result[key] not in (None, "", [], {}):
            parts.append("%s=%s" % (key, _short(result[key])))
        if len(parts) >= 3:
            break
    if not parts:
        for key in list(result)[:3]:
            parts.append("%s=%s" % (key, _short(result[key])))
    return _clip(", ".join(parts), BRIEF_LIMIT) or "ok"


# ---------------------------------------------------------------------------
# socket commands
# ---------------------------------------------------------------------------

@command("flow_list")
def cmd_flow_list(params):
    """Every saved flow, with its description and parameters."""
    flows = list_flows()
    return {"dir": flows_dir(), "flows": flows, "count": len(flows)}


@command("flow_run")
def cmd_flow_run(params):
    """Run a saved flow by ``name``, or an inline ``flow`` object.

    Blender steps dispatch straight through the command registry (we are already
    on the main thread here), service steps go out over HTTP.  Linear,
    fail-fast, and the response summarises every step that ran.
    """
    inline = params.get("flow")
    if inline is not None:
        if not isinstance(inline, dict):
            raise ForgeError("'flow' must be a flow object; pass 'name' to run a saved one.")
        doc = validate_flow(inline)
    else:
        doc = read_flow(params.get("name"))
    return run_flow(doc, params.get("params"))


# ---------------------------------------------------------------------------
# panel state
# ---------------------------------------------------------------------------

class ForgeFlowParam(PropertyGroup):
    """One editable parameter of the selected flow (edited as text, typed on run)."""

    name: StringProperty(default="")
    value: StringProperty(name="Value", default="")
    unit: StringProperty(default="")
    description: StringProperty(default="")

    def label_text(self):
        return "%s (%s)" % (self.name, self.unit) if self.unit else self.name


class ForgeFlowEntry(PropertyGroup):
    name: StringProperty(default="")
    description: StringProperty(default="")
    path: StringProperty(default="")
    steps: IntProperty(default=0)
    error: StringProperty(default="")


class ForgeFlowStep(PropertyGroup):
    """One row of the step list in the editor: what it does, in that order."""

    label: StringProperty(default="")
    kind: StringProperty(default="")
    op: StringProperty(default="")

    def line(self):
        label = str(self.label or "").strip()
        return label or ("%s %s" % (self.kind or "?", self.op or "?"))


class ForgeFlowsProps(PropertyGroup):
    flows: CollectionProperty(type=ForgeFlowEntry)
    params: CollectionProperty(type=ForgeFlowParam)
    steps: CollectionProperty(type=ForgeFlowStep)
    selected: StringProperty(name="Flow", default="")
    status: StringProperty(default="")
    status_is_error: BoolProperty(default=False)
    summary: StringProperty(default="")
    busy: BoolProperty(default=False)
    loaded: BoolProperty(default=False)
    #: The editor is off by default: the Flows box is a row of Run buttons
    #: first and a workbench second.
    editing: BoolProperty(
        name="Edit",
        description="Change this flow's defaults and the order of its steps",
        default=False,
    )
    edit_description: StringProperty(
        name="Description",
        description="What this flow does, in the artist's own words",
        default="",
    )
    #: The flow being edited, as JSON, so reordering and deleting cost nothing
    #: until Save is pressed and a half-finished edit can always be abandoned by
    #: selecting the flow again.
    edit_json: StringProperty(default="")
    dirty: BoolProperty(default=False)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_flows", None)


def set_status(props, message, error=False):
    if props is None:
        return
    text = str(message or "").strip()
    props.status = text.splitlines()[0][:400] if text else ""
    props.status_is_error = bool(error)
    if error and text:
        print("[Forge/Flows]", text)


def refresh(props):
    """Rescan the flows folder into the panel's collections."""
    props.flows.clear()
    props.loaded = True
    for entry in list_flows():
        item = props.flows.add()
        item.name = str(entry.get("name") or "")
        item.description = str(entry.get("description") or "")
        item.path = str(entry.get("path") or "")
        item.steps = int(entry.get("steps") or 0)
        item.error = str(entry.get("error") or "")
    names = [item.name for item in props.flows]
    if props.selected not in names:
        props.selected = names[0] if names else ""
    select(props, props.selected)
    return len(props.flows)


def select(props, name):
    """Point the param list (and the editor) at ``name``'s flow."""
    props.selected = str(name or "")
    props.params.clear()
    props.steps.clear()
    props.edit_json = ""
    props.edit_description = ""
    props.dirty = False
    if not props.selected:
        return
    try:
        doc = read_flow(props.selected)
    except ForgeError:
        return
    load_editor(props, doc)


def load_editor(props, doc):
    """Fill the param rows, the step rows and the working copy from ``doc``."""
    props.params.clear()
    for key, spec in (doc.get("params") or {}).items():
        item = props.params.add()
        item.name = str(key)
        value = spec.get("value")
        item.value = "" if value is None else (
            json.dumps(value) if isinstance(value, (dict, list)) else str(value))
        item.unit = str(spec.get("unit") or "")
        item.description = str(spec.get("description") or "")
    props.edit_description = str(doc.get("description") or "")
    props.edit_json = json.dumps(doc)
    props.dirty = False
    sync_steps(props, doc)


def sync_steps(props, doc):
    """Rebuild the step rows from a flow document."""
    props.steps.clear()
    for index, step in enumerate(doc.get("steps") or []):
        if not isinstance(step, dict):
            continue
        item = props.steps.add()
        item.label = step_label(step, index)
        item.kind = str(step.get("kind") or "")
        item.op = str(step.get("op") or "")
    return len(props.steps)


def editor_doc(props):
    """The working copy being edited, or the file if nothing is loaded yet."""
    raw = str(props.edit_json or "").strip()
    if not raw:
        return read_flow(props.selected)
    try:
        doc = json.loads(raw)
    except ValueError:
        return read_flow(props.selected)
    if not isinstance(doc, dict):
        return read_flow(props.selected)
    return doc


def _store_editor(props, doc):
    props.edit_json = json.dumps(doc)
    props.dirty = True
    sync_steps(props, doc)


def move_step(props, index, offset):
    """Move step ``index`` by ``offset`` in the working copy."""
    doc = editor_doc(props)
    steps = doc.get("steps") or []
    target = index + offset
    if index < 0 or index >= len(steps):
        raise ForgeError("There is no step %d in this flow." % (index + 1))
    if target < 0 or target >= len(steps):
        raise ForgeError("Step %d is already %s." % (index + 1,
                                                     "first" if offset < 0 else "last"))
    steps[index], steps[target] = steps[target], steps[index]
    doc["steps"] = steps
    _store_editor(props, doc)
    return target


def delete_step(props, index):
    """Remove step ``index`` from the working copy (Save writes it)."""
    doc = editor_doc(props)
    steps = doc.get("steps") or []
    if index < 0 or index >= len(steps):
        raise ForgeError("There is no step %d in this flow." % (index + 1))
    removed = step_label(steps[index], index)
    del steps[index]
    doc["steps"] = steps
    _store_editor(props, doc)
    return removed


def apply_edits(props, doc):
    """Fold the editable fields — description and param defaults — into ``doc``.

    Values come back as the strings the panel edits, so each one is typed like
    the default it replaces: a flow whose ``wedges`` was 4 keeps a number, not
    the text "6".
    """
    doc["description"] = str(props.edit_description or "").strip()
    params = doc.get("params") or {}
    for item in props.params:
        key = str(item.name or "")
        spec = params.get(key)
        if not isinstance(spec, dict):
            continue
        spec["value"] = _coerce(item.value, spec.get("value"), key)
    if params:
        doc["params"] = params
    return doc


def save_flow(props):
    """Write the working copy back over its file. Returns the path written."""
    name = str(props.selected or "").strip()
    if not name:
        raise ForgeError("Pick a flow first.")
    doc = apply_edits(props, editor_doc(props))

    steps = doc.get("steps") or []
    if len(steps) < 2:
        # The same rule flow_save enforces: a one-step flow is a button for
        # something that is already a button, and it hides what it does.
        raise ForgeError(
            "A flow needs at least two steps — with one step left there is "
            "nothing to save that the panel cannot already do. Undo the "
            "deletion by selecting the flow again.")
    validate_flow(doc, source=name)

    path = flow_path(name)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        raise ForgeError("Could not write %s: %s" % (path, exc))
    props.edit_json = json.dumps(doc)
    props.dirty = False
    return path


def panel_overrides(props):
    """The param collection as a ``{name: value}`` dict of strings."""
    return dict((item.name, item.value) for item in props.params if item.name)


# ---------------------------------------------------------------------------
# main-thread marshalling
#
# A flow run from the panel happens on a worker thread so a 30-second /segment
# does not freeze Blender.  Blender steps cannot run there, so they are pushed
# back to the main thread through this queue, which the operator's own poll
# timer drains.  (The socket path never touches any of this: it is already on
# the main thread.)
# ---------------------------------------------------------------------------

_MAIN_JOBS = queue.Queue()


def _on_main_thread():
    return threading.current_thread() is threading.main_thread()


def drain_main_jobs():
    """Run everything the worker has queued. Main thread only."""
    while True:
        try:
            job = _MAIN_JOBS.get_nowait()
        except queue.Empty:
            return
        try:
            job["value"] = job["fn"]()
        except Exception as exc:  # noqa: BLE001 - handed back to the worker
            job["error"] = exc
        finally:
            job["event"].set()


def call_on_main(fn, timeout=MAIN_THREAD_TIMEOUT):
    if _on_main_thread() or bpy.app.background:
        return fn()
    job = {"fn": fn, "value": None, "error": None, "event": threading.Event()}
    _MAIN_JOBS.put(job)
    if not job["event"].wait(timeout):
        raise ForgeError(
            "Blender did not get to this step within %.0f seconds. It may be busy "
            "with a modal operator — finish what is open in the viewport and try "
            "again." % timeout)
    if job["error"] is not None:
        raise job["error"]
    return job["value"]


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class FORGE_OT_flow_refresh(Operator):
    bl_idname = "forge.flow_refresh"
    bl_label = "Refresh Flows"
    bl_description = "Re-read the flows folder"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        try:
            count = refresh(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}
        set_status(props, "%d flow(s) in %s" % (count, flows_dir()))
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_flow_select(Operator):
    bl_idname = "forge.flow_select"
    bl_label = "Select Flow"
    bl_description = "Show this flow's parameters"
    bl_options = {"REGISTER"}

    name: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        select(props, self.name)
        set_status(props, "")
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_flow_run(Operator):
    bl_idname = "forge.flow_run"
    bl_label = "Run Flow"
    bl_description = "Replay this saved sequence of Forge operations"
    bl_options = {"REGISTER"}

    name: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        name = str(self.name or props.selected or "").strip()
        if not name:
            set_status(props, "Pick a flow first.", error=True)
            return {"CANCELLED"}
        if name != props.selected:
            select(props, name)

        try:
            doc = read_flow(name)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        overrides = panel_overrides(props)
        props.busy = True
        props.summary = ""
        set_status(props, "Running %s ..." % name)

        def work():
            return run_flow(doc, overrides,
                            blender=lambda op, args: call_on_main(
                                lambda: call_blender(op, args)))

        def done(value, error):
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            report = value or {}
            steps = report.get("steps") or []
            props.summary = "  ".join(
                "%d %s" % (entry["index"] + 1, entry["label"]) for entry in steps)[:400]
            set_status(props, "%s: %d step(s) in %.1fs"
                       % (name, len(steps), (report.get("duration_ms") or 0) / 1000.0))

        _run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_flow_step_move(Operator):
    bl_idname = "forge.flow_step_move"
    bl_label = "Move Step"
    bl_description = "Move this step up or down. Nothing is written until you press Save"
    bl_options = {"REGISTER"}

    index: IntProperty(default=0)
    direction: StringProperty(default="UP")

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        offset = -1 if str(self.direction).upper() == "UP" else 1
        try:
            move_step(props, int(self.index), offset)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}
        set_status(props, "Reordered — press Save to keep it.")
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_flow_step_delete(Operator):
    bl_idname = "forge.flow_step_delete"
    bl_label = "Delete Step"
    bl_description = "Remove this step. Nothing is written until you press Save"
    bl_options = {"REGISTER"}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        try:
            removed = delete_step(props, int(self.index))
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}
        set_status(props, "Removed '%s' — press Save to keep it." % removed)
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_flow_save(Operator):
    bl_idname = "forge.flow_save"
    bl_label = "Save Flow"
    bl_description = "Write the description, the default values and the step order back to the flow file"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy and bool(props.selected)

    def execute(self, context):
        props = get_props(context)
        try:
            path = save_flow(props)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}
        try:
            refresh(props)
        except ForgeError:
            pass
        set_status(props, "Saved %s" % os.path.basename(path))
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_flow_revert(Operator):
    bl_idname = "forge.flow_revert"
    bl_label = "Discard Changes"
    bl_description = "Forget the edits and re-read the flow from disk"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy and bool(props.selected)

    def execute(self, context):
        props = get_props(context)
        select(props, props.selected)
        set_status(props, "Back to what is on disk.")
        _tag_redraw()
        return {"FINISHED"}


def _run_async(work, done):
    """PartForge's async shape, with the main-thread queue drained each tick."""
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

    threading.Thread(target=worker, name="ForgeFlow", daemon=True).start()

    def poll():
        drain_main_jobs()  # the worker's Blender steps run here, on the main thread
        if not box["done"]:
            _tag_redraw()
            return 0.1
        try:
            done(box["value"], box["error"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tag_redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.05)


_CLASSES = (
    ForgeFlowParam,
    ForgeFlowEntry,
    ForgeFlowStep,
    ForgeFlowsProps,
    FORGE_OT_flow_refresh,
    FORGE_OT_flow_select,
    FORGE_OT_flow_run,
    FORGE_OT_flow_step_move,
    FORGE_OT_flow_step_delete,
    FORGE_OT_flow_save,
    FORGE_OT_flow_revert,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_flows = bpy.props.PointerProperty(type=ForgeFlowsProps)


def unregister():
    try:
        del bpy.types.Scene.forge_flows
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
