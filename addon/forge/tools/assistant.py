"""Forge Assistant: the chat box at the top of the N-panel.

This module owns no intelligence at all.  It collects what the artist typed
plus a little scene context, posts it to the assistant bridge on
``127.0.0.1:8901``, polls until the answer lands, and writes the exchange into
a scene-level chat log the panel draws.

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


class ForgeAssistantProps(PropertyGroup):
    message: StringProperty(
        name="Message",
        description="Tell the assistant what you want, in your own words",
        default="",
    )
    log: CollectionProperty(type=ForgeChatTurn)
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

        payload = {
            "message": message,
            "context": collect_context(context),
            "conversation": self.conversation or "continue",
        }
        ask_url = bridge_url("/ask")
        job_url_base = bridge_url("/job/")

        append_turn(props, "you", message)
        props.message = ""
        props.busy = True
        props.job_id = ""
        props.last_cost = ""
        set_status(props, "Thinking ...")

        # The worker thread must not touch bpy, so it publishes the job id here
        # and the timer below copies it onto the props for the Stop button.
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
                if state.get("state") in ("done", "error", "cancelled"):
                    state["job_id"] = job_id
                    return state

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            props.job_id = ""
            if error is not None:
                append_turn(props, "forge", str(error))
                set_status(props, str(error), error=True)
                return
            state = value or {}
            if state.get("state") == "done":
                append_turn(props, "forge", state.get("reply") or "(no reply)")
                props.turns += 1
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
            if _alive(props) and shared.get("job_id") and not props.job_id:
                props.job_id = shared["job_id"]

        _run_async(work, done, props, tick=tick)
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
    ForgeAssistantProps,
    FORGE_OT_assistant_send,
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
