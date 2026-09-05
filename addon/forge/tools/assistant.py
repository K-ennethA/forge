"""Forge Assistant: the chat box at the top of the N-panel.

This module owns no intelligence at all.  It collects what the artist typed
plus a little scene context, posts it to the assistant bridge on
``127.0.0.1:8901``, polls until the answer lands, and writes the exchange into
a scene-level chat log the panel draws.

Phase 6c adds one thing to that: an optional reference image.  The artist picks
a file with Blender's own file browser, the path (never the pixels) rides along
in ``context.image_path`` on the next ``/ask``, and the field clears once the
answer lands — one message per attachment, re-attach to send it again.

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
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..prefs import pref
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


def request_json(url, payload=None, timeout=HTTP_TIMEOUT, method=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method is None:
        method = "POST" if data is not None else "GET"

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

class ForgeChatTurn(PropertyGroup):
    """One line of the visible chat log."""

    role: StringProperty(name="Role", default="you")  # "you" | "forge"
    text: StringProperty(name="Text", default="")


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
    job_id: StringProperty(default="")
    last_cost: StringProperty(default="")
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


def append_turn(props, role, text):
    """Add a line and trim the log to the last ``MAX_TURNS`` exchanges."""
    entry = props.log.add()
    entry.role = role
    entry.text = str(text or "").strip()[:4000]
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

def _dimensions_mm(obj):
    try:
        return [round(float(v) * 1000.0, 1) for v in obj.dimensions]
    except (AttributeError, TypeError, ValueError):
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

    try:
        out["mode"] = str(context.mode)
    except (AttributeError, TypeError):
        pass
    out["blender_version"] = bpy.app.version_string
    return out


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

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
        message = str(props.message or "").strip()
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

        payload = {
            "message": message,
            "context": scene_context,
            "conversation": self.conversation or "continue",
        }
        ask_url = bridge_url("/ask")
        job_url_base = bridge_url("/job/")

        append_turn(props, "you", message)
        props.message = ""
        props.busy = True
        props.job_id = ""
        props.last_cost = ""
        props.activity.clear()
        set_status(props, "Thinking ...")

        # The worker thread must not touch bpy, so it publishes the job id and
        # the activity list here and the timer below copies both onto the props.
        shared = {}

        def work():
            started = time.monotonic()
            reply = request_json(ask_url, payload)
            job_id = str(reply.get("job_id") or "")
            if not job_id:
                raise BridgeError("The assistant did not start the job.")
            shared["job_id"] = job_id
            while True:
                if time.monotonic() - started > JOB_DEADLINE:
                    raise BridgeError("The assistant did not answer in time.")
                time.sleep(POLL_INTERVAL)
                state = request_json(job_url_base + job_id, method="GET")
                shared["activity"] = state.get("activity") or []
                if state.get("state") in ("done", "error", "cancelled"):
                    state["job_id"] = job_id
                    return state

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            props.job_id = ""
            # The finished job keeps its activity, so the last lines stay
            # readable after the answer lands.
            set_activity(props, (value or {}).get("activity")
                         if isinstance(value, dict) else shared.get("activity"))
            if error is not None:
                append_turn(props, "forge", str(error))
                set_status(props, str(error), error=True)
                return
            state = value or {}
            if state.get("state") == "done":
                append_turn(props, "forge", state.get("reply") or "(no reply)")
                props.turns += 1
                if attachment:
                    # One message per attachment. It has been looked at now;
                    # leaving it on would silently re-send it every turn.
                    props.image_path = ""
                cost = state.get("cost_usd")
                bits = []
                if isinstance(cost, (int, float)):
                    bits.append("$%.4f" % cost)
                duration = state.get("duration_ms")
                if isinstance(duration, (int, float)):
                    bits.append("%.1fs" % (duration / 1000.0))
                props.last_cost = "  ".join(bits)
                set_status(props, "Answered.")
            elif state.get("state") == "cancelled":
                append_turn(props, "forge", "Stopped.")
                set_status(props, "Stopped.")
            else:
                message = state.get("error") or "The assistant errored."
                append_turn(props, "forge", message)
                set_status(props, message, error=True)

        def tick():
            if not _alive(props):
                return
            if shared.get("job_id") and not props.job_id:
                props.job_id = shared["job_id"]
            set_activity(props, shared.get("activity"))

        _run_async(work, done, props, tick=tick)
        return {"FINISHED"}


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
