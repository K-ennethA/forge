"""The health row at the top of the Forge tab, and the Start Services button.

Five things decide whether Forge works, and until now the artist found out which
one was not by reading an error three clicks deep in some other box.  This
module answers the question up front:

* the **geometry service** (8765) — builds the shapes;
* the **assistant bridge** (8901) — the chat box's other half;
* **picture to 3D** (8902, meshgen) — optional: the only row that is allowed to
  be down on a working machine, because the 18.5 GB of models is a download the
  artist may simply not have made;
* the **command socket** (9876) — how Claude drives Blender; it lives in this
  process, so if the panel is drawing at all we can simply read its status;
* **sign-in** — whether the Claude CLI is installed and logged in, taken from
  the bridge's ``/health`` (``claude_cli.found`` and ``last_auth_error``).  It
  is never probed by running the CLI: that costs money and seconds.

Nothing here blocks a draw.  A refresh operator does the two HTTP calls on a
worker thread and hands the answer back through a ``bpy.app.timers`` callback,
exactly the way PartForge and the Assistant already do it.

Start Services runs the repo's own ``start_forge.ps1`` hidden, rather than
reimplementing what it does.  One file decides how those two processes are
launched; the button and the double-click get identical behaviour, and a port
that is already answering is left strictly alone.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from ..prefs import meshgen_url, pref, repo_root, start_script_path, service_url
from .partforge import _tag_redraw

#: One state vocabulary for every dot, so the panel has one icon table.
UNKNOWN, UP, DOWN, WARN = "unknown", "up", "down", "warn"

STATE_ICONS = {
    UP: "CHECKMARK",
    DOWN: "CANCEL",
    WARN: "ERROR",
    UNKNOWN: "RADIOBUT_OFF",
}

#: Health calls are answered instantly or not at all; a long timeout here would
#: only make a dead port feel like a slow one.
HEALTH_TIMEOUT = 4.0
PROBE_TIMEOUT = 0.6

#: How long the Start button waits for the script.  ``start_forge.ps1`` waits up
#: to 25 s per port and then sleeps 4 s before exiting.
START_TIMEOUT = 120.0

#: Only meaningful on Windows; 0 elsewhere so the same call site works.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

NOT_CHECKED = "Press Refresh to check"


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------

def _url(base, path):
    base = str(base or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        base = "http://" + base
    return base + "/" + str(path).lstrip("/")


def bridge_base():
    return str(pref("assistant_url") or "http://127.0.0.1:8901").strip()


def url_port(url, fallback):
    """The port a base URL names, for the port probe and the panel's labels."""
    text = str(url or "").strip()
    if text and "://" not in text:
        text = "http://" + text
    try:
        parsed = urllib.parse.urlparse(text)
        return int(parsed.port or fallback)
    except (ValueError, TypeError):
        return fallback


def probe_port(port, host="127.0.0.1", timeout=PROBE_TIMEOUT):
    """Is anything listening? Cheap enough to run before spawning a shell."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def get_json(url, timeout=HEALTH_TIMEOUT):
    """GET a JSON object, or raise ``OSError``/``ValueError``. Worker-thread safe."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"},
                                     method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
    return parsed if isinstance(parsed, dict) else {}


def meshgen_base():
    return str(meshgen_url() or "http://127.0.0.1:8902").strip()


def meshgen_row(payload):
    """``(state, detail)`` from meshgen's ``/health``.

    Three honest answers rather than a green light: ready with the backend
    named, a warning naming the first missing weight (the models are a separate
    18.5 GB download, and "down" would read as broken when it is only absent),
    or misconfigured with the service's own reason.
    """
    status = str(payload.get("status") or "").lower()
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    name = str(backend.get("name") or "") or "no backend"
    if status == "models_missing":
        missing = [m for m in (payload.get("missing") or []) if isinstance(m, dict)]
        first = str(missing[0].get("what") or "a model file") if missing else "model files"
        return WARN, "%d file(s) to download: %s" % (len(missing) or 1, first)
    if status == "misconfigured":
        return WARN, str(payload.get("error") or "misconfigured")[:120]
    if status:
        return UP, "%s ready" % name
    return DOWN, "no answer"


def poll_health(service_health_url, bridge_health_url, meshgen_health_url=""):
    """Every health call, as one plain dict. Runs on a worker thread; no bpy."""
    out = {}

    try:
        payload = get_json(service_health_url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["service"] = (DOWN, _reason(exc))
    else:
        version = payload.get("build123d") or payload.get("version") or ""
        out["service"] = (UP, ("build123d %s" % version) if version else "ready")

    if meshgen_health_url:
        try:
            payload = get_json(meshgen_health_url)
        except (urllib.error.URLError, OSError, ValueError):
            # Not an error the artist has to fix: this one is optional, so the
            # detail says what it would take rather than what went wrong.
            out["meshgen"] = (DOWN, "not running - press Start services")
        else:
            out["meshgen"] = meshgen_row(payload)

    try:
        payload = get_json(bridge_health_url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["bridge"] = (DOWN, _reason(exc))
        out["cli"] = (UNKNOWN, "the assistant is not running")
        return out

    out["bridge"] = (UP, "ready" if not payload.get("busy") else "working")
    if payload.get("queued"):
        out["bridge"] = (UP, "working, one message waiting")

    cli = payload.get("claude_cli") if isinstance(payload.get("claude_cli"), dict) else {}
    if not cli.get("found"):
        out["cli"] = (DOWN, str(cli.get("hint") or "The Claude CLI is not installed."))
    elif payload.get("last_auth_error"):
        # Additive field from the bridge: the last turn failed on sign-in.  It
        # is the honest signal — nothing here ever spends a turn to find out.
        out["cli"] = (WARN, "Signed out — open a terminal, type claude, run /login")
    else:
        out["cli"] = (UP, "Claude %s" % (cli.get("version") or "CLI"))

    cost = payload.get("session_cost_usd")
    if isinstance(cost, (int, float)):
        out["session_cost_usd"] = float(cost)
    return out


def _reason(exc):
    reason = getattr(exc, "reason", None) or exc
    return str(reason)[:120]


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

class ForgeServicesProps(PropertyGroup):
    """What the health row draws. Five states, five one-line explanations."""

    service_state: StringProperty(default=UNKNOWN)
    service_detail: StringProperty(default=NOT_CHECKED)
    bridge_state: StringProperty(default=UNKNOWN)
    bridge_detail: StringProperty(default=NOT_CHECKED)
    meshgen_state: StringProperty(default=UNKNOWN)
    meshgen_detail: StringProperty(default=NOT_CHECKED)
    cli_state: StringProperty(default=UNKNOWN)
    cli_detail: StringProperty(default=NOT_CHECKED)
    status: StringProperty(default="")
    status_is_error: BoolProperty(default=False)
    busy: BoolProperty(default=False)
    checked: BoolProperty(default=False)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_services", None)


def set_status(props, message, error=False):
    if props is None:
        return
    text = str(message or "").strip()
    props.status = text.splitlines()[0][:400] if text else ""
    props.status_is_error = bool(error)


def apply_health(props, health):
    """Copy a :func:`poll_health` result onto the props. Main thread."""
    if props is None:
        return
    for key, prefix in (("service", "service"), ("bridge", "bridge"),
                        ("meshgen", "meshgen"), ("cli", "cli")):
        state, detail = (health or {}).get(key) or (UNKNOWN, NOT_CHECKED)
        setattr(props, prefix + "_state", str(state))
        setattr(props, prefix + "_detail", str(detail)[:400])
    props.checked = True

    # The cost footer rides along on the same poll rather than costing a call
    # of its own; the Assistant box owns the property, this just fills it in.
    cost = (health or {}).get("session_cost_usd")
    if isinstance(cost, (int, float)):
        from . import assistant

        chat = assistant.get_props()
        if chat is not None:
            chat.session_cost = float(cost)


def socket_row():
    """``(state, detail)`` for the command socket — read straight out of it."""
    # Imported here, not at module scope: ``forge.server`` imports this package,
    # and the add-on's own import order must not depend on which of the two got
    # there first.
    from .. import server as forge_server

    status = forge_server.get_status()
    if status.get("running"):
        return UP, "port %d" % int(status.get("port") or 9876)
    return DOWN, "stopped (port %d)" % int(status.get("port") or 9876)


def rows(props):
    """The five (label, state, detail) tuples the panel draws, in order."""
    socket_state, socket_detail = socket_row()
    if props is None:
        return [("Shapes", UNKNOWN, NOT_CHECKED),
                ("Assistant", UNKNOWN, NOT_CHECKED),
                ("Picture to 3D", UNKNOWN, NOT_CHECKED),
                ("Blender link", socket_state, socket_detail),
                ("Sign-in", UNKNOWN, NOT_CHECKED)]
    return [
        ("Shapes", props.service_state, props.service_detail),
        ("Assistant", props.bridge_state, props.bridge_detail),
        ("Picture to 3D", props.meshgen_state, props.meshgen_detail),
        ("Blender link", socket_state, socket_detail),
        ("Sign-in", props.cli_state, props.cli_detail),
    ]


#: Rows whose absence stops Forge working.  "Picture to 3D" is deliberately not
#: one of them: it is an optional 18.5 GB download, and a machine without it is
#: a working machine.
REQUIRED_ROWS = ("Shapes", "Assistant")


# ---------------------------------------------------------------------------
# starting the background programs
# ---------------------------------------------------------------------------

def _powershell():
    """The PowerShell to run ``start_forge.ps1`` with, or ``""``."""
    import shutil

    for name in ("powershell.exe", "powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform.startswith("win"):
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                 "System32", "WindowsPowerShell", "v1.0",
                                 "powershell.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def start_services(script, root, timeout=START_TIMEOUT):
    """Run ``start_forge.ps1`` hidden and return its own report.

    Worker thread only (it waits on a process).  Returns
    ``{"ran": bool, "code": int?, "output": str}``; the caller decides what the
    status line says by re-probing the ports afterwards, because the script's
    text is for a terminal and the panel wants one sentence.
    """
    shell = _powershell()
    if not shell:
        raise RuntimeError(
            "PowerShell was not found, so Forge cannot start the background "
            "programs for you. Double-click start_forge.cmd in the forge folder "
            "instead.")

    argv = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
    proc = subprocess.Popen(
        argv,
        cwd=root or os.path.dirname(script),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _err = proc.communicate()
        return {"ran": True, "code": None, "timed_out": True,
                "output": (out or b"").decode("utf-8", "replace")}
    return {"ran": True, "code": proc.returncode,
            "output": (out or b"").decode("utf-8", "replace")}


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _alive(props):
    try:
        return props is not None and props.busy in (True, False)
    except (ReferenceError, AttributeError):
        return False


def _run_async(work, done, props):
    """The house pattern: worker thread, timer delivery, inline in background."""
    if bpy.app.background:
        try:
            done(work(), None)
        except Exception as exc:  # noqa: BLE001
            if _alive(props):
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

    threading.Thread(target=worker, name="ForgeServices", daemon=True).start()

    def poll():
        if not box["done"]:
            return 0.25
        try:
            done(box["value"], box["error"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tag_redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.1)


class FORGE_OT_services_refresh(Operator):
    bl_idname = "forge.services_refresh"
    bl_label = "Check Services"
    bl_description = "Ask the shape service and the assistant whether they are running"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        service = service_url("/health")
        bridge = _url(bridge_base(), "/health")
        meshgen = _url(meshgen_base(), "/health")
        props.busy = True
        set_status(props, "Checking ...")

        def work():
            return poll_health(service, bridge, meshgen)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            apply_health(props, value)
            down = [label for label, state, _detail in rows(props) if state == DOWN]
            required_down = [label for label in down if label in REQUIRED_ROWS]
            if required_down:
                set_status(props, "Not running: %s" % ", ".join(required_down), error=True)
            elif down:
                # Only the optional row is down: say so without the red box, so
                # a machine that simply has no picture models does not look broken.
                set_status(props, "Ready. Not running: %s." % ", ".join(down))
            else:
                set_status(props, "Everything is running.")

        _run_async(work, done, props)
        return {"FINISHED"}


class FORGE_OT_services_start(Operator):
    bl_idname = "forge.services_start"
    bl_label = "Start Services"
    bl_description = (
        "Start the background programs Forge needs (the shape service and the "
        "assistant). Anything already running is left alone"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy

    def execute(self, context):
        props = get_props(context)
        script = start_script_path()
        if not script:
            set_status(props,
                       "start_forge.ps1 was not found next to the add-on. Open the "
                       "forge folder and double-click start_forge.cmd instead.",
                       error=True)
            return {"CANCELLED"}

        root = repo_root()
        service_port = url_port(service_url(), 8765)
        bridge_port = url_port(bridge_base(), 8901)
        service_health = service_url("/health")
        bridge_health = _url(bridge_base(), "/health")
        meshgen_health = _url(meshgen_base(), "/health")

        # meshgen is deliberately NOT in this map: start_forge.ps1 starts it
        # only when the models are installed, so requiring it here would report
        # a failure on every machine that never downloaded them.  The health
        # poll below still reports it, so the dot is right either way.
        before = {"Shapes": probe_port(service_port),
                  "Assistant": probe_port(bridge_port)}
        if all(before.values()):
            props.busy = False
            set_status(props, "Both background programs were already running.")

            def work_check():
                return poll_health(service_health, bridge_health, meshgen_health)

            def done_check(value, error):
                if _alive(props) and error is None:
                    apply_health(props, value)

            _run_async(work_check, done_check, props)
            return {"FINISHED"}

        props.busy = True
        set_status(props, "Starting %s ..." % " and ".join(
            name for name, up in before.items() if not up))

        def work():
            report = start_services(script, root)
            report["after"] = {"Shapes": probe_port(service_port),
                               "Assistant": probe_port(bridge_port)}
            report["health"] = poll_health(service_health, bridge_health,
                                           meshgen_health)
            return report

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                set_status(props, str(error), error=True)
                return
            report = value or {}
            apply_health(props, report.get("health"))
            after = report.get("after") or {}
            started = [name for name, up in after.items() if up and not before.get(name)]
            failed = [name for name, up in after.items() if not up]
            if failed:
                set_status(props,
                           "%s did not start. Open the forge folder and run "
                           "start_forge.cmd to see why." % " and ".join(failed),
                           error=True)
                return
            if started:
                set_status(props, "Started %s." % " and ".join(started))
            else:
                set_status(props, "Everything was already running.")

        _run_async(work, done, props)
        return {"FINISHED"}


class FORGE_OT_revert_ai(Operator):
    """The Assistant box's "Revert last AI action" button.

    Deliberately just ``ed.undo``: every socket command pushes a named
    checkpoint before it runs (see ``tools/registry.py``), so one undo step is
    one thing the assistant did, and Ctrl+Z does exactly the same job.
    """

    bl_idname = "forge.revert_ai"
    bl_label = "Revert last AI action"
    bl_description = (
        "Undo the last thing the assistant changed in the scene. The same as "
        "pressing Ctrl+Z in the viewport"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        chat = None
        try:
            from . import assistant

            chat = assistant.get_props(context)
        except Exception:  # noqa: BLE001
            chat = None
        try:
            bpy.ops.ed.undo()
        except RuntimeError as exc:
            message = ("Nothing left to undo (%s)." % exc) if "undo" in str(exc).lower() \
                else str(exc)
            if chat is not None:
                from . import assistant

                assistant.set_status(chat, message, error=True)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        if chat is not None:
            from . import assistant

            assistant.set_status(chat, "Reverted the last change. Press again to go "
                                       "back further.")
        _tag_redraw()
        return {"FINISHED"}


_CLASSES = (
    ForgeServicesProps,
    FORGE_OT_services_refresh,
    FORGE_OT_services_start,
    FORGE_OT_revert_ai,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_services = bpy.props.PointerProperty(type=ForgeServicesProps)


def unregister():
    try:
        del bpy.types.Scene.forge_services
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
