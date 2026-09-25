"""Forge Assistant: the chat box at the top of the N-panel.

This module owns no intelligence at all.  It collects what the artist typed
plus a little scene context, posts it to the assistant bridge on
``127.0.0.1:8901``, polls until the answer lands, and writes the exchange into
a scene-level chat log the panel draws.

Phase 6c adds one thing to that: an optional reference image.  The artist picks
a file with Blender's own file browser, the path (never the pixels) rides along
in ``context.image_path`` on the next ``/ask``, and the field clears once the
answer lands — one message per attachment, re-attach to send it again.

The speed selector rides the same way: ``Fast`` / ``Smart`` / ``Deepest`` is a
per-message choice sent as ``model`` on every ``/ask`` (chips included), and a
scene starts on whatever the add-on preference says — no environment variable,
because an artist will never find one.

Async exactly the way PartForge does it: the operator returns immediately, a
worker thread does the HTTP, and a ``bpy.app.timers`` callback delivers the
result on the main thread.  Nothing here ever blocks Blender's UI, and every
failure ends up in ``props.status`` where the artist can see it instead of in a
console they are not looking at.
"""

import json
import os
import threading
import time
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

from ..prefs import ASSISTANT_MODELS, DEFAULT_ASSISTANT_MODEL, pref
from .partforge import _tag_redraw

#: Shown verbatim when the bridge is not answering.  It names the script the
#: repo actually ships, because "start the bridge" means nothing to an artist.
BRIDGE_DOWN = "Assistant not running — double-click start_forge.cmd in the forge folder"

#: How many exchanges the panel keeps.  Six is about what fits in a sidebar
#: before the artist has to scroll past their own question.
MAX_TURNS = 6

#: How many activity lines the panel shows under the busy indicator.  Enough to
#: see what it is doing now and what it just did; not enough to push the chat
#: log off the screen.
ACTIVITY_LINES = 5

#: Activity kinds -> the icon each line gets.
ACTIVITY_ICONS = {
    "tool": "TOOL_SETTINGS",
    "text": "SMALL_CAPS",
    "status": "SORTTIME",
}

#: The three things an artist asks for over and over, as buttons.  The text is
#: what actually gets sent — a chip is not a special code path, it is the
#: sentence they would have typed, so the reply reads the same either way and
#: they learn what to ask for next time.
QUICK_ACTIONS = (
    ("check", "Check print", "CHECKMARK",
     "Run the print checks on the current part and explain anything that fails "
     "in plain words."),
    ("segment", "Segment to fit", "MOD_BOOLEAN",
     "Segment the current part so every piece fits my printer bed, and lay the "
     "pieces out."),
    ("export", "Export STL", "EXPORT",
     "Export the current part as an STL to the project's exports folder and "
     "tell me where it is."),
)

#: How many characters of a reply the chat log shows before the expand button
#: is the only way to read the rest.
LOG_LINE_WIDTH = 38
LOG_MAX_LINES = 24

POLL_INTERVAL = 0.4
#: The bridge answers /ask and /job instantly; only the CLI behind it is slow.
HTTP_TIMEOUT = 20.0
#: Give up on a job that never finishes (the bridge has its own, shorter, cap).
JOB_DEADLINE = 900.0


class BridgeError(Exception):
    """The assistant bridge refused or could not be reached."""


# ---------------------------------------------------------------------------
# HTTP (stdlib only, like every other network call in this add-on)
# ---------------------------------------------------------------------------

def bridge_url(path=""):
    base = str(pref("assistant_url") or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8901"
    if "://" not in base:
        base = "http://" + base
    if not path:
        return base
    return base + "/" + str(path).lstrip("/")


def _bridge_token():
    """The bridge requires ``Authorization: Bearer <token>`` on every POST.

    The token lives beside the bridge (assistant/.bridge-token, overridable
    with FORGE_ASSISTANT_TOKEN_FILE) and changes on every bridge start, so it
    is re-read per request rather than cached.
    """
    path = os.environ.get("FORGE_ASSISTANT_TOKEN_FILE")
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, os.pardir, os.pardir,
                            "assistant", ".bridge-token")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def request_json(url, payload=None, timeout=HTTP_TIMEOUT, method=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method is None:
        method = "POST" if data is not None else "GET"
    if method != "GET":
        token = _bridge_token()
        if token:
            headers["Authorization"] = "Bearer " + token

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
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
        message = ""
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                message = str(parsed.get("error") or "")
        except ValueError:
            message = body.strip()[:400]
        raise BridgeError(message or ("The assistant returned HTTP %s." % exc.code))
    except urllib.error.URLError:
        raise BridgeError(BRIDGE_DOWN)
    except OSError:
        raise BridgeError(BRIDGE_DOWN)

    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        raise BridgeError("The assistant sent something that was not JSON.")
    if not isinstance(parsed, dict):
        raise BridgeError("The assistant sent something unexpected.")
    return parsed


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

#: The identifiers of :data:`ASSISTANT_MODELS`, in the order they are drawn.
MODEL_IDS = tuple(item[0] for item in ASSISTANT_MODELS)


def default_model():
    """The model a scene starts on: the add-on preference, sanity-checked."""
    value = str(pref("assistant_model") or "").strip().lower()
    return value if value in MODEL_IDS else DEFAULT_ASSISTANT_MODEL


def _model_get(self):
    """Read the selector, falling back to the preference until it is touched.

    An ``EnumProperty``'s ``default`` is fixed when the class is registered, and
    the preference is not readable then (nor would a later change to it reach an
    already-registered property).  So the choice is stored by hand under its own
    key and "never chosen" reads through to the preference — which means the
    value the panel draws is always the value that will be sent.
    """
    stored = self.get("model_choice")
    fallback = MODEL_IDS.index(default_model())
    if stored is None:
        return fallback
    try:
        index = int(stored)
    except (TypeError, ValueError):
        return fallback
    return index if 0 <= index < len(MODEL_IDS) else fallback


def _model_set(self, value):
    self["model_choice"] = int(value)


class ForgeChatTurn(PropertyGroup):
    """One line of the visible chat log."""

    role: StringProperty(name="Role", default="you")  # "you" | "forge"
    text: StringProperty(name="Text", default="")
    #: True when this turn came from a buddy check-in rather than from the
    #: artist typing.  The panel labels those differently, because an answer
    #: nobody asked for reads as a bug unless it says where it came from.
    check_in: BoolProperty(default=False)


class ForgeActivityLine(PropertyGroup):
    """One line of "what it is doing right now", straight off the bridge."""

    kind: StringProperty(default="status")  # "tool" | "text" | "status"
    label: StringProperty(default="")


class ForgeAssistantProps(PropertyGroup):
    message: StringProperty(
        name="Message",
        description="Tell the assistant what you want, in your own words",
        default="",
    )
    model: EnumProperty(
        name="Speed",
        description=("How hard the assistant thinks about your message. It "
                     "rides with each message, so you can change it any time"),
        items=ASSISTANT_MODELS,
        get=_model_get,
        set=_model_set,
    )
    image_path: StringProperty(
        name="Reference image",
        description=("A sketch or photo for the assistant to look at. It travels "
                     "with your next message only, then clears"),
        default="",
        subtype="FILE_PATH",
    )
    log: CollectionProperty(type=ForgeChatTurn)
    activity: CollectionProperty(type=ForgeActivityLine)
    status: StringProperty(name="Status", default="")
    status_is_error: BoolProperty(default=False)
    busy: BoolProperty(default=False)
    #: True while this message is waiting behind another one on the bridge.
    queued: BoolProperty(default=False)
    job_id: StringProperty(default="")
    last_cost: StringProperty(default="")
    #: Everything this conversation has cost, in dollars, straight off the
    #: bridge.  Reset by New Conversation, because that is what "this session"
    #: means to the person reading it.
    session_cost: FloatProperty(default=0.0)
    turns: IntProperty(default=0)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_assistant", None)


def set_status(props, message, error=False):
    if props is None:
        return
    text = str(message or "").strip()
    props.status = text.splitlines()[0][:400] if text else ""
    props.status_is_error = bool(error)
    if error and text:
        print("[Forge/Assistant]", text)


def append_turn(props, role, text, check_in=False):
    """Add a line and trim the log to the last ``MAX_TURNS`` exchanges."""
    entry = props.log.add()
    entry.role = role
    entry.text = str(text or "").strip()[:4000]
    entry.check_in = bool(check_in)
    while len(props.log) > MAX_TURNS * 2:
        props.log.remove(0)
    return entry


def set_activity(props, entries):
    """Replace the visible activity lines with the bridge's latest few.

    Only the tail is kept: a 200-entry list would be a scroll bar, and the
    artist wants the last thing that happened, not a transcript.
    """
    if props is None:
        return 0
    entries = [entry for entry in (entries or []) if isinstance(entry, dict)]
    tail = entries[-ACTIVITY_LINES:]
    if len(props.activity) == len(tail) and all(
            line.label == str(entry.get("label") or "")
            for line, entry in zip(props.activity, tail)):
        return len(tail)  # unchanged: do not churn the UI every poll
    props.activity.clear()
    for entry in tail:
        line = props.activity.add()
        line.kind = str(entry.get("kind") or "status")
        line.label = str(entry.get("label") or "")[:200]
    return len(tail)


# ---------------------------------------------------------------------------
# the attached reference image (Phase 6c)
# ---------------------------------------------------------------------------

#: What Claude Code's Read tool renders, and what `load_reference` will open.
#: The bridge checks the same list server-side; this half exists so a mistyped
#: path is caught in the sidebar instead of one turn later.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def resolve_image_path(path):
    """An attachment as an absolute path (``//`` blend-relative included)."""
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    try:
        text = bpy.path.abspath(text)
    except Exception:  # noqa: BLE001 - bpy.path dislikes some odd input
        pass
    text = os.path.expandvars(os.path.expanduser(text))
    return os.path.abspath(os.path.normpath(text))


def image_problem(path):
    """Why this attachment cannot be sent, in the artist's words, or ``""``."""
    resolved = resolve_image_path(path)
    if not resolved:
        return ""
    # Folder first: a directory is a directory whatever it is called.
    if os.path.isdir(resolved):
        return "%s is a folder, not a picture." % os.path.basename(resolved.rstrip("\\/"))
    if os.path.splitext(resolved)[1].lower() not in IMAGE_EXTENSIONS:
        return ("%s is not a picture Forge can read. Attach a %s file."
                % (os.path.basename(resolved) or resolved,
                   " or ".join(IMAGE_EXTENSIONS)))
    if not os.path.isfile(resolved):
        return "There is no file at %s." % resolved
    return ""


def image_label(path):
    """The chip text: just the filename, because the path is 90 characters."""
    resolved = resolve_image_path(path)
    return os.path.basename(resolved.rstrip("\\/")) if resolved else ""


def _alive(props):
    try:
        return props is not None and props.busy in (True, False)
    except (ReferenceError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# scene context — what the assistant is told about the file
# ---------------------------------------------------------------------------

#: How many selected objects travel with a message.  A sculptor with 400 objects
#: selected has told us nothing by selecting them; the first ten plus a count is
#: the whole signal.
MAX_SELECTED = 10


def _dimensions_mm(obj):
    try:
        return [round(float(v) * 1000.0, 1) for v in obj.dimensions]
    except (AttributeError, TypeError, ValueError):
        return None


def _selected_names(context):
    """The names of the selected objects, capped, never raising.

    Guarded at every step: ``selected_objects`` does not exist on every context
    (``--background`` and some override contexts have none), and an object can
    go away between the list and the read.
    """
    names = []
    try:
        selected = list(getattr(context, "selected_objects", None) or [])
    except (AttributeError, TypeError, RuntimeError):
        selected = []
    if not selected:
        view_layer = getattr(context, "view_layer", None)
        try:
            selected = [obj for obj in getattr(view_layer, "objects", [])
                        if obj.select_get()]
        except (AttributeError, TypeError, RuntimeError, ReferenceError):
            selected = []
    for obj in selected[:MAX_SELECTED]:
        try:
            names.append(obj.name)
        except (AttributeError, ReferenceError):
            continue
    if len(selected) > MAX_SELECTED:
        names.append("... and %d more" % (len(selected) - MAX_SELECTED))
    return names


def _brush_summary(context):
    """``{"name", "size", "strength"}`` for the active sculpt/paint brush.

    Only meaningful in a paint-like mode, so only collected there: the brush
    that happens to be loaded while the artist is in Object Mode is noise, and
    noise in the context is a sentence the assistant will believe.
    """
    mode = str(getattr(context, "mode", "") or "").upper()
    if not (mode.startswith("SCULPT") or mode.startswith("PAINT")):
        return None
    tool_settings = getattr(context, "tool_settings", None)
    if tool_settings is None:
        return None
    for attribute in ("sculpt", "vertex_paint", "weight_paint", "image_paint",
                      "gpencil_paint", "curves_sculpt"):
        paint = getattr(tool_settings, attribute, None)
        brush = getattr(paint, "brush", None)
        if brush is None:
            continue
        try:
            summary = {"name": brush.name}
        except (AttributeError, ReferenceError):
            continue
        for key, source in (("size", "size"), ("strength", "strength")):
            value = getattr(brush, source, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                summary[key] = round(float(value), 4)
        symmetry = [axis.upper() for axis in ("x", "y", "z")
                    if getattr(paint, "use_symmetry_%s" % axis, False) is True]
        if symmetry:
            summary["symmetry"] = symmetry
        return summary
    return None


def collect_context(context=None):
    """A small, honest summary of the scene, built on the main thread."""
    context = context or bpy.context
    out = {}
    scene = getattr(context, "scene", None)
    if scene is None:
        return out

    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active = getattr(active, "active", None)
    if active is not None:
        size = _dimensions_mm(active)
        out["active_object"] = "%s (%s%s)" % (
            active.name, active.type,
            (", %g x %g x %g mm" % tuple(size)) if size else "")

    names = []
    for obj in list(scene.objects)[:40]:
        size = _dimensions_mm(obj)
        names.append("%s [%s%s]" % (obj.name, obj.type,
                                    (" %g x %g x %g mm" % tuple(size)) if size else ""))
    if names:
        out["objects"] = names
        if len(scene.objects) > len(names):
            out["objects"].append("... and %d more" % (len(scene.objects) - len(names)))

    partforge = getattr(scene, "forge_partforge", None)
    script_path = str(getattr(partforge, "script_path", "") or "").strip()
    if script_path:
        out["script_path"] = bpy.path.abspath(script_path)

    # Phase 8: where the artist actually IS. Mode, what they have selected and
    # (in sculpt or paint) the brush in their hand. Every one of these is a
    # cheap attribute read and every one is guarded, because this runs on every
    # message including the ones sent from a headless harness with no viewport,
    # no selection and no brush.
    try:
        out["mode"] = str(context.mode)
    except (AttributeError, TypeError):
        obj = getattr(getattr(context, "view_layer", None), "objects", None)
        obj = getattr(obj, "active", None)
        if obj is not None:
            out["mode"] = str(getattr(obj, "mode", "OBJECT"))

    selected = _selected_names(context)
    if selected:
        out["selected_objects"] = selected

    brush = _brush_summary(context)
    if brush:
        out["brush"] = brush

    out["blender_version"] = bpy.app.version_string
    return out


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def chosen_model(props):
    """The model the next message runs on, as the bridge's own identifier.

    Read off the props rather than the preference so the artist's per-message
    choice is what travels; the preference only decides where a scene starts.
    """
    try:
        value = str(getattr(props, "model", "") or "").strip().lower()
    except (AttributeError, ReferenceError, TypeError):
        value = ""
    return value if value in MODEL_IDS else default_model()


def model_label(props):
    """``"Fast"`` — what the selector currently reads, for a status line."""
    current = chosen_model(props)
    for identifier, label, _description in ASSISTANT_MODELS:
        if identifier == current:
            return label
    return current


def cost_footer(props):
    """``"This session: $0.42"``, or ``""`` when nothing has cost anything yet."""
    try:
        total = float(getattr(props, "session_cost", 0.0) or 0.0)
    except (TypeError, ValueError):
        return ""
    if total <= 0.0:
        return ""
    return "This session: $%.2f" % total


def send_message(context, props, message, conversation="continue",
                 check_in=False, log_text=None, extra_context=None,
                 on_reply=None):
    """Send one message the way the Send button does. Returns an operator set.

    Every path into the assistant goes through here — the Send button, the
    quick-action chips, the buddy's check-ins, anything added later — so the
    attachment rules, the chat log and the queue behaviour cannot drift apart
    between them.

    ``check_in`` marks both turns as a buddy check-in (the panel labels them);
    ``log_text`` is what the chat log shows for the outgoing turn when the real
    message is a wall of paths and numbers nobody wants in a sidebar;
    ``extra_context`` is merged over the collected scene context; ``on_reply``
    is called with the finished reply text so the buddy can remember what it
    already said.
    """
    message = str(message or "").strip()
    if not message:
        set_status(props, "Type what you want first.", error=True)
        return {"CANCELLED"}

    # The attachment is checked here, before anything is sent: a missing
    # file has to be a sentence in the sidebar, not a turn spent watching
    # the assistant fail to open it.
    attachment = resolve_image_path(props.image_path)
    problem = image_problem(props.image_path)
    if problem:
        set_status(props, problem, error=True)
        return {"CANCELLED"}

    scene_context = collect_context(context)
    if attachment:
        scene_context["image_path"] = attachment
    if isinstance(extra_context, dict):
        scene_context.update(extra_context)

    payload = {
        "message": message,
        "context": scene_context,
        "conversation": conversation or "continue",
        # Rides with every message, chips included: the selector is a property
        # of the message, not a mode the panel is left in.
        "model": chosen_model(props),
    }
    ask_url = bridge_url("/ask")
    job_url_base = bridge_url("/job/")

    append_turn(props, "you", log_text if log_text else message, check_in=check_in)
    props.message = ""
    props.busy = True
    props.queued = False
    props.job_id = ""
    props.last_cost = ""
    props.activity.clear()
    set_status(props, "Thinking ...")

    # The worker thread must not touch bpy, so it publishes the job id, the
    # state and the activity list here and the timer below copies them onto
    # the props.
    shared = {}

    def work():
        started = time.monotonic()
        reply = request_json(ask_url, payload)
        job_id = str(reply.get("job_id") or "")
        if not job_id:
            raise BridgeError("The assistant did not start the job.")
        shared["job_id"] = job_id
        # The bridge queues one message behind a running turn instead of
        # refusing it; when it does, this is where the panel learns to say so.
        shared["state"] = str(reply.get("state") or "running")
        while True:
            if time.monotonic() - started > JOB_DEADLINE:
                raise BridgeError("The assistant did not answer in time.")
            time.sleep(POLL_INTERVAL)
            state = request_json(job_url_base + job_id, method="GET")
            shared["activity"] = state.get("activity") or []
            shared["state"] = str(state.get("state") or "running")
            if state.get("state") in ("done", "error", "cancelled", "timeout",
                                      "over_budget"):
                state["job_id"] = job_id
                return state

    def done(value, error):
        if not _alive(props):
            return
        props.busy = False
        props.queued = False
        props.job_id = ""
        # The finished job keeps its activity, so the last lines stay
        # readable after the answer lands.
        set_activity(props, (value or {}).get("activity")
                     if isinstance(value, dict) else shared.get("activity"))
        state = value if isinstance(value, dict) else {}
        total = state.get("session_cost_usd")
        if isinstance(total, (int, float)):
            props.session_cost = float(total)
        if error is not None:
            append_turn(props, "forge", str(error), check_in=check_in)
            set_status(props, str(error), error=True)
            return
        if state.get("state") == "done":
            reply = state.get("reply") or "(no reply)"
            append_turn(props, "forge", reply, check_in=check_in)
            if on_reply is not None:
                try:
                    on_reply(reply)
                except Exception:  # noqa: BLE001 - a note is never worth a failure
                    traceback.print_exc()
            props.turns += 1
            if attachment:
                # One message per attachment. It has been looked at now;
                # leaving it on would silently re-send it every turn.
                props.image_path = ""
            cost = state.get("cost_usd")
            bits = []
            if isinstance(cost, (int, float)):
                bits.append("$%.4f" % cost)
                if not isinstance(total, (int, float)):
                    # An older bridge with no running total: at least keep the
                    # footer honest by adding this turn to it ourselves.
                    props.session_cost = float(props.session_cost) + float(cost)
            duration = state.get("duration_ms")
            if isinstance(duration, (int, float)):
                bits.append("%.1fs" % (duration / 1000.0))
            props.last_cost = "  ".join(bits)
            set_status(props, "Answered.")
        elif state.get("state") == "cancelled":
            append_turn(props, "forge", "Stopped.")
            set_status(props, "Stopped.")
        else:
            failure = state.get("error") or "The assistant errored."
            append_turn(props, "forge", failure)
            set_status(props, failure, error=True)

    def tick():
        if not _alive(props):
            return
        if shared.get("job_id") and not props.job_id:
            props.job_id = shared["job_id"]
        queued = shared.get("state") == "queued"
        if queued != bool(props.queued):
            props.queued = queued
            set_status(props, "Queued — waiting for the current answer ..."
                       if queued else "Thinking ...")
        set_activity(props, shared.get("activity"))

    _run_async(work, done, props, tick=tick)
    return {"FINISHED"}


class FORGE_OT_assistant_send(Operator):
    bl_idname = "forge.assistant_send"
    bl_label = "Send"
    bl_description = "Ask the Forge assistant to do it, or to tell you how"
    bl_options = {"REGISTER"}

    conversation: StringProperty(default="continue", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        return send_message(context, props, props.message,
                            conversation=self.conversation or "continue")


class FORGE_OT_assistant_quick(Operator):
    """One of the three canned jobs above the chat box.

    It types the sentence for the artist and sends it — same endpoint, same
    conversation, same attachment rules.  The wording is deliberately the
    wording a person would use, so the reply is the reply they would have got.
    """

    bl_idname = "forge.assistant_quick"
    bl_label = "Quick Action"
    bl_options = {"REGISTER"}

    action: StringProperty(default="check", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    @classmethod
    def description(cls, context, properties):
        for key, _label, _icon, text in QUICK_ACTIONS:
            if key == getattr(properties, "action", ""):
                return "Send: %s" % text
        return "Send a canned request to the assistant"

    def execute(self, context):
        props = get_props(context)
        text = quick_action_text(self.action)
        if not text:
            set_status(props, "Unknown quick action %r." % self.action, error=True)
            return {"CANCELLED"}
        return send_message(context, props, text)


def quick_action_text(key):
    """The sentence a chip sends, or ``""`` for a key that is not one."""
    for name, _label, _icon, text in QUICK_ACTIONS:
        if name == key:
            return text
    return ""


class FORGE_OT_assistant_show_reply(Operator):
    """Read one exchange in full, in a popup, when the log truncated it.

    The sidebar log shows the first couple of dozen lines of a reply because a
    sidebar is 40 characters wide; a numbered handoff list ("press N, then ...")
    routinely runs past that.  This is the rest of it — plain wrapped text, no
    scrolling widget, because a popup that always works beats a text editor that
    sometimes does.
    """

    bl_idname = "forge.assistant_show_reply"
    bl_label = "Full reply"
    bl_description = "Show this message in full, in a window you can read"
    bl_options = {"REGISTER", "INTERNAL"}

    index: IntProperty(default=-1, options={"HIDDEN"})

    def _entry(self, context):
        props = get_props(context)
        if props is None or not len(props.log):
            return None
        index = int(self.index)
        if index < 0 or index >= len(props.log):
            index = len(props.log) - 1
        return props.log[index]

    def invoke(self, context, event):
        entry = self._entry(context)
        if entry is None:
            self.report({"WARNING"}, "There is nothing in the chat log yet.")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=700)

    def draw(self, context):
        # Read the entry again rather than stashing it in invoke(): the dialog
        # redraws, and the log is the only place that text has to live.
        layout = self.layout
        entry = self._entry(context)
        if entry is None:
            layout.label(text="That message is no longer in the log.", icon="INFO")
            return
        you = entry.role == "you"
        layout.label(text="You said:" if you else "Forge said:",
                     icon="USER" if you else "LIGHT")
        column = layout.column(align=True)
        column.scale_y = 0.8
        for line in _wrap_text(entry.text, 96):
            column.label(text=line)

    def execute(self, context):
        # Nothing to apply: the dialog IS the operator.  Headless (and the OK
        # button) land here, and both should simply succeed.
        entry = self._entry(context)
        return {"FINISHED"} if entry is not None else {"CANCELLED"}


def _wrap_text(text, width):
    """Word wrap for the popup. The panel has its own; this one is not drawing
    a sidebar, so it keeps blank lines (a numbered list needs its gaps)."""
    lines = []
    for paragraph in str(text or "").splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in paragraph.split():
            candidate = (current + " " + word).strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [""]


def is_truncated(text, width=LOG_LINE_WIDTH, max_lines=LOG_MAX_LINES):
    """Did the sidebar have to cut this message short?

    Counts the way the panel's own wrapper does (blank lines dropped, since
    ``layout.label`` draws nothing for them), so the expand button appears
    exactly when there is something hidden behind it.
    """
    lines = [line for line in _wrap_text(text, width) if line]
    return len(lines) > max_lines


class FORGE_OT_assistant_clear_image(Operator):
    bl_idname = "forge.assistant_clear_image"
    bl_label = "Remove Reference Image"
    bl_description = "Don't send the attached picture with the next message"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and bool(props.image_path)

    def execute(self, context):
        props = get_props(context)
        props.image_path = ""
        set_status(props, "Reference image removed.")
        return {"FINISHED"}


class FORGE_OT_assistant_new(Operator):
    bl_idname = "forge.assistant_new"
    bl_label = "New Conversation"
    bl_description = "Forget what was said so far and start fresh"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        props.log.clear()
        props.activity.clear()
        props.turns = 0
        props.last_cost = ""
        # "This session" starts again here, on both sides: the bridge zeroes its
        # own total on /new, and the footer must not show the old one until the
        # next answer comes back to correct it.
        props.session_cost = 0.0
        url = bridge_url("/new")

        def work():
            return request_json(url, {})

        def done(_value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                # The log is already clear; the bridge just did not hear about it.
                set_status(props, str(error), error=True)
                return
            set_status(props, "New conversation.")

        props.busy = True
        set_status(props, "Starting a new conversation ...")
        _run_async(work, done, props)
        return {"FINISHED"}


class FORGE_OT_assistant_cancel(Operator):
    bl_idname = "forge.assistant_cancel"
    bl_label = "Stop"
    bl_description = "Stop the assistant working on the current message"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and bool(props.busy)

    def execute(self, context):
        props = get_props(context)
        job_id = str(props.job_id or "")
        if not job_id:
            props.busy = False
            set_status(props, "Stopped.")
            return {"FINISHED"}
        url = bridge_url("/cancel/" + job_id)

        def work():
            return request_json(url, {})

        def done(_value, error):
            if not _alive(props):
                return
            if error is not None:
                set_status(props, str(error), error=True)

        _run_async(work, done, props, clear_busy=False)
        return {"FINISHED"}


class FORGE_OT_assistant_health(Operator):
    bl_idname = "forge.assistant_health"
    bl_label = "Check Assistant"
    bl_description = "Ask the assistant bridge whether it (and the Claude CLI) are there"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        url = bridge_url("/health")

        def work():
            return request_json(url, method="GET", timeout=10.0)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            cli = (value or {}).get("claude_cli") or {}
            if not cli.get("found"):
                set_status(props, cli.get("hint") or "The Claude CLI was not found.",
                           error=True)
                return
            set_status(props, "Ready (Claude %s)" % (cli.get("version") or "CLI"))

        props.busy = True
        set_status(props, "Checking the assistant ...")
        _run_async(work, done, props)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# async plumbing
# ---------------------------------------------------------------------------

def _run_async(work, done, props, clear_busy=True, tick=None):
    """PartForge's pattern, with the job id lifted out for the Stop button.

    In ``--background`` there is no timer loop worth waiting on, so it runs
    inline — which is exactly what the headless suite needs.
    """
    if bpy.app.background:
        try:
            done(work(), None)
        except Exception as exc:  # noqa: BLE001
            if clear_busy and _alive(props):
                props.busy = False
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

    threading.Thread(target=worker, name="ForgeAssistant", daemon=True).start()

    def poll():
        if not box["done"]:
            if tick is not None:
                try:
                    tick()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                _tag_redraw()
            return POLL_INTERVAL
        try:
            done(box["value"], box["error"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tag_redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.2)


_CLASSES = (
    ForgeChatTurn,
    ForgeActivityLine,
    ForgeAssistantProps,
    FORGE_OT_assistant_send,
    FORGE_OT_assistant_quick,
    FORGE_OT_assistant_show_reply,
    FORGE_OT_assistant_clear_image,
    FORGE_OT_assistant_new,
    FORGE_OT_assistant_cancel,
    FORGE_OT_assistant_health,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_assistant = bpy.props.PointerProperty(type=ForgeAssistantProps)


def unregister():
    try:
        del bpy.types.Scene.forge_assistant
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
