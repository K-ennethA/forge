"""Headless add-on tests for Phase 6 (the Assistant chat box).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_assistant.py

Needs no geometry service, no Claude CLI and no network beyond loopback: the
"bridge" here is a twenty-line ``http.server`` inside this file that answers
``/ask``, ``/job/<id>``, ``/new`` and ``/cancel`` with canned JSON.  That is the
whole point — this suite is about the *panel*, not the assistant.  Whether the
real bridge builds the right command line is ``assistant/tests/test_bridge.py``'s
job, and the two never overlap.

The port is 9882, one above Phase 5's socket port, and it is an HTTP port rather
than the add-on's command socket: the assistant does not speak the socket
protocol at all.

Three things are actually being proved:

1. the properties and operators register and hang off the scene;
2. Send round-trips a canned reply into the chat log (in ``--background`` the
   operator's async helper runs inline, so this is deterministic);
3. with nothing listening, the status line says *exactly* the sentence
   ``start_forge.cmd`` is named in — because a beginner reads that line and has
   to be able to act on it without asking anyone.
"""

import json
import os
import socket as socketlib
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9882  # not 9876..9881 (live session + phases 2-5)
BRIDGE_URL = "http://127.0.0.1:%d" % PORT
DEAD_URL = "http://127.0.0.1:%d" % (PORT + 1)  # nothing ever listens here

CANNED_REPLY = (
    "Done - I cut it into 4 wedges, each about 120 mm across, so every piece "
    "fits your 256 mm bed."
)

_RESULTS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


# ---------------------------------------------------------------------------
# the fake bridge
# ---------------------------------------------------------------------------

class FakeBridge(object):
    """Answers exactly the four routes the panel calls, and records them."""

    def __init__(self, port):
        self.calls = []
        self.jobs = {}
        self.next_state = "done"
        self.session_reset = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    return {}

            def do_GET(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                outer.calls.append(("GET", path, None))
                if path == "/health":
                    self._send(200, {"status": "ok", "busy": False,
                                     "claude_cli": {"found": True, "path": "fake",
                                                    "version": "9.9.9 (Fake)"}})
                    return
                if path.startswith("/job/"):
                    job_id = path[len("/job/"):]
                    self._send(200, outer.jobs.get(job_id, {"state": "error",
                                                            "error": "no such job"}))
                    return
                self._send(404, {"error": "nope"})

            def do_POST(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                payload = self._body()
                outer.calls.append(("POST", path, payload))
                if path == "/ask":
                    job_id = "job-%d" % (len(outer.jobs) + 1)
                    if outer.next_state == "busy":
                        self._send(409, {"error": "The assistant is still working "
                                                  "on your last message."})
                        return
                    outer.jobs[job_id] = outer._result(job_id)
                    self._send(200, {"job_id": job_id, "state": "running"})
                    return
                if path == "/new":
                    outer.session_reset += 1
                    self._send(200, {"status": "ok", "session": None})
                    return
                if path.startswith("/cancel/"):
                    job_id = path[len("/cancel/"):]
                    outer.jobs[job_id] = {"state": "cancelled", "job_id": job_id}
                    self._send(200, outer.jobs[job_id])
                    return
                self._send(404, {"error": "nope"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def _result(self, job_id):
        if self.next_state == "error":
            return {"state": "error", "job_id": job_id,
                    "error": "The geometry service is not running."}
        return {"state": "done", "job_id": job_id, "reply": CANNED_REPLY,
                "session_id": "sess-panel-1", "cost_usd": 0.0042,
                "duration_ms": 1234}

    def asks(self):
        return [payload for method, path, payload in self.calls
                if method == "POST" and path == "/ask"]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def point_at(url):
    """Aim the add-on preference (or its fallback) at ``url``."""
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["assistant_url"] = url
    forge_prefs._FALLBACK.assistant_url = url
    entry = None
    try:
        entry = bpy.context.preferences.addons.get(forge_prefs.ADDON_ID)
    except (AttributeError, TypeError):
        entry = None
    if entry is not None and entry.preferences is not None:
        try:
            entry.preferences.assistant_url = url
        except (AttributeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import assistant

    props = assistant.get_props(bpy.context)
    if not check("scene.forge_assistant exists", props is not None):
        return None
    for name in ("message", "log", "status", "status_is_error", "busy",
                 "job_id", "last_cost", "turns"):
        check("the chat props carry %s" % name, hasattr(props, name))
    for name in ("assistant_send", "assistant_new", "assistant_cancel",
                 "assistant_health"):
        check("forge.%s is registered" % name, hasattr(bpy.ops.forge, name))

    cls = getattr(bpy.types, "VIEW3D_PT_forge_assistant", None)
    if check("the Assistant panel class is registered", cls is not None):
        check("it lives in the Forge sidebar tab", cls.bl_category == "Forge",
              str(cls.bl_category))
        check("it is a top-level panel, not a sub-panel",
              not getattr(cls, "bl_parent_id", ""), str(getattr(cls, "bl_parent_id", "")))
        check("it sorts to the top of the tab", getattr(cls, "bl_order", 99) == 0,
              str(getattr(cls, "bl_order", None)))

    from forge.ui import panels

    order = [c.__name__ for c in panels._CLASSES]
    # Since the UI batch exactly one thing sits above it: the status row that
    # says whether the assistant is even running. Everything that DOES anything
    # is still below the chat box.
    check("only the status row is registered above it",
          order[:2] == ["VIEW3D_PT_forge_health", "VIEW3D_PT_forge_assistant"],
          str(order[:3]))
    check("and it is above every panel that does work",
          order.index("VIEW3D_PT_forge_assistant")
          < min(order.index(name) for name in
                ("VIEW3D_PT_forge_partforge", "VIEW3D_PT_forge_flows",
                 "VIEW3D_PT_forge_rigforge", "VIEW3D_PT_forge_model")),
          str(order[:6]))

    from forge.prefs import DEFAULTS

    check("the bridge URL is an add-on preference with a loopback default",
          DEFAULTS.get("assistant_url") == "http://127.0.0.1:8901",
          str(DEFAULTS.get("assistant_url")))
    check("ForgePreferences declares it",
          "assistant_url" in getattr(bpy.types.AddonPreferences, "__annotations__", {})
          or hasattr(bpy.context.preferences.addons.get("forge").preferences,
                     "assistant_url"))
    return props


def test_context_is_collected():
    section("the context the panel sends")
    from forge.tools import assistant

    bpy.ops.mesh.primitive_cube_add(size=0.1)
    cube = bpy.context.view_layer.objects.active
    cube.name = "TestCup"

    context = assistant.collect_context(bpy.context)
    check("it names the active object", "TestCup" in str(context.get("active_object")),
          str(context.get("active_object")))
    check("with its size in millimetres, not metres",
          "mm" in str(context.get("active_object")), str(context.get("active_object")))
    check("it lists the scene objects", any("TestCup" in entry
                                            for entry in context.get("objects") or []),
          str(context.get("objects"))[:200])
    check("and says which Blender this is", bool(context.get("blender_version")),
          str(context.get("blender_version")))

    scene = bpy.context.scene
    scene.forge_partforge.script_path = os.path.join(TESTS_DIR, "nothing.py")
    context = assistant.collect_context(bpy.context)
    check("the PartForge script path rides along when one is set",
          "nothing.py" in str(context.get("script_path")), str(context.get("script_path")))
    scene.forge_partforge.script_path = ""
    note("context keys: %s" % sorted(context))


def test_send_round_trip(props, fake):
    section("Send against the fake bridge")
    point_at(BRIDGE_URL)
    props.log.clear()
    props.message = "segment this into 4 so it fits my bed"

    result = bpy.ops.forge.assistant_send()
    check("the Send operator finished", "FINISHED" in result, str(result))
    check("the message field was cleared", props.message == "", props.message)
    check("the panel is not left busy", props.busy is False, str(props.busy))

    entries = [(entry.role, entry.text) for entry in props.log]
    check("the chat log holds the question and the answer", len(entries) == 2,
          str(entries))
    if len(entries) == 2:
        check("the question is attributed to the artist",
              entries[0][0] == "you" and "segment this" in entries[0][1],
              str(entries[0]))
        check("the answer is attributed to Forge and is the canned reply",
              entries[1][0] == "forge" and entries[1][1] == CANNED_REPLY,
              str(entries[1]))
    check("the status says it answered", props.status == "Answered.", props.status)
    check("the status is not an error", props.status_is_error is False)
    check("the cost and duration are shown",
          "$0.0042" in props.last_cost and "1.2s" in props.last_cost, props.last_cost)
    check("the turn counter moved", props.turns == 1, str(props.turns))

    asks = fake.asks()
    if check("the bridge received one /ask", len(asks) == 1, str(len(asks))):
        payload = asks[0]
        check("it carried the message verbatim",
              payload.get("message") == "segment this into 4 so it fits my bed",
              str(payload.get("message")))
        check("it carried the scene context",
              isinstance(payload.get("context"), dict)
              and "active_object" in payload["context"],
              str(sorted(payload.get("context") or {})))
        check("and asked to continue the conversation",
              payload.get("conversation") == "continue",
              str(payload.get("conversation")))
    check("the panel polled the job",
          any(path.startswith("/job/") for method, path, _p in fake.calls
              if method == "GET"),
          str([c[1] for c in fake.calls]))


def test_log_is_trimmed(props, fake):
    section("the log keeps only the last few exchanges")
    from forge.tools import assistant

    props.log.clear()
    for index in range(assistant.MAX_TURNS + 3):
        props.message = "question %d" % index
        bpy.ops.forge.assistant_send()
    check("the log is capped at %d exchanges" % assistant.MAX_TURNS,
          len(props.log) == assistant.MAX_TURNS * 2, str(len(props.log)))
    check("and it kept the newest, not the oldest",
          "question %d" % (assistant.MAX_TURNS + 2) in props.log[-2].text,
          props.log[-2].text)


def test_new_conversation(props, fake):
    section("New Conversation")
    before = fake.session_reset
    result = bpy.ops.forge.assistant_new()
    check("the operator finished", "FINISHED" in result, str(result))
    check("the visible log was cleared", len(props.log) == 0, str(len(props.log)))
    check("the turn counter reset", props.turns == 0, str(props.turns))
    check("and the bridge was told to forget the session",
          fake.session_reset == before + 1, str(fake.session_reset))
    check("the status says so", "New conversation" in props.status, props.status)


def test_health(props, fake):
    section("the health button")
    result = bpy.ops.forge.assistant_health()
    check("the operator finished", "FINISHED" in result, str(result))
    check("and reports the CLI version it was told about",
          "9.9.9" in props.status, props.status)
    check("without flagging an error", props.status_is_error is False, props.status)


def test_bridge_error_is_shown(props, fake):
    section("an error from the bridge reaches the artist")
    fake.next_state = "error"
    props.log.clear()
    props.message = "do the impossible"
    bpy.ops.forge.assistant_send()
    check("the failure is in the status line, not a traceback",
          props.status_is_error and "geometry service" in props.status, props.status)
    check("and in the chat log where they are already looking",
          len(props.log) == 2 and "geometry service" in props.log[1].text,
          str([e.text for e in props.log]))
    check("the panel is usable again", props.busy is False)
    fake.next_state = "done"


def test_busy_is_reported(props, fake):
    section("a busy bridge")
    fake.next_state = "busy"
    props.log.clear()
    props.message = "one more"
    bpy.ops.forge.assistant_send()
    check("the 409 becomes a plain-language status",
          props.status_is_error and "still working" in props.status, props.status)
    fake.next_state = "done"


def test_unreachable_bridge_says_exactly_the_right_thing(props):
    section("nothing listening - the sentence a beginner needs")
    from forge.tools import assistant

    point_at(DEAD_URL)
    props.log.clear()
    props.message = "hello?"
    bpy.ops.forge.assistant_send()

    expected = ("Assistant not running — double-click start_forge.cmd "
                "in the forge folder")
    check("the module's constant is that exact sentence",
          assistant.BRIDGE_DOWN == expected, repr(assistant.BRIDGE_DOWN))
    check("and it names the script the repo actually ships",
          os.path.isfile(os.path.join(ADDON_DIR, os.pardir, "start_forge.cmd")),
          "start_forge.cmd missing from the repo root")
    check("the status line is that sentence, character for character",
          props.status == expected, repr(props.status))
    check("it is flagged as an error so the panel goes red",
          props.status_is_error is True)
    check("the chat log carries it too",
          len(props.log) == 2 and props.log[1].text == expected,
          str([e.text for e in props.log]))
    check("and the panel is not stuck busy", props.busy is False)

    # the health button has to say the same thing, not something different
    props.status = ""
    bpy.ops.forge.assistant_health()
    check("the health button says the same sentence", props.status == expected,
          repr(props.status))
    point_at(BRIDGE_URL)


def test_panel_draw(props, fake):
    section("the panel draws without exploding")
    import re

    from forge.ui import panels

    props.log.clear()
    props.message = "make me a bowl"
    bpy.ops.forge.assistant_send()

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_assistant")[1].split("\nclass ")[0]
    used = sorted(set(re.findall(r'\.prop\(chat,\s*"([a-z_]+)"', body)))
    check("the Assistant panel draws at least one property", bool(used), str(used))
    unknown = [name for name in used if not hasattr(props, name)]
    check("every property the Assistant panel draws exists", not unknown, str(unknown))
    # the sibling suites scan for .prop(props, ...) and .prop(rf, ...) and assert
    # those names live on *their* groups, so this panel must not borrow either.
    check("it does not borrow another panel's binding name",
          not re.search(r'\.prop\((props|rf|ra),\s*"', body),
          "the Assistant panel must bind its state to `chat`")
    operators = sorted(set(re.findall(r'\.operator\(\s*"(forge\.assistant_[a-z_]+)"',
                                      body)))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every button maps to a registered operator", not missing, str(missing))
    for name in ("forge.assistant_send", "forge.assistant_new",
                 "forge.assistant_cancel", "forge.assistant_health"):
        check("the panel offers %s" % name, name in operators, str(operators))

    # The wrapper the log is drawn through is the one thing draw() does that can
    # actually fail on real data, so exercise it directly (background Blender
    # has no region to run draw() itself in).
    wrapped = panels._wrap(CANNED_REPLY, 38)
    check("the reply wraps into sidebar-width lines",
          wrapped and all(len(line) <= 38 for line in wrapped), str(wrapped))
    check("and nothing is lost in the wrapping",
          " ".join(wrapped).split() == CANNED_REPLY.split(), str(wrapped))
    check("an empty reply still wraps to something drawable",
          panels._wrap("", 38) == [""], str(panels._wrap("", 38)))

    check("the older panels are still registered",
          all(getattr(bpy.types, name, None) is not None
              for name in ("VIEW3D_PT_forge_partforge", "VIEW3D_PT_forge_rigforge",
                           "VIEW3D_PT_forge_server")))


def test_prefs_and_urls():
    section("the bridge URL preference")
    from forge.tools import assistant

    point_at("http://127.0.0.1:8901")
    check("bridge_url() builds the ask endpoint",
          assistant.bridge_url("/ask") == "http://127.0.0.1:8901/ask",
          assistant.bridge_url("/ask"))
    point_at("127.0.0.1:9999")
    check("a URL without a scheme gets http:// put on it",
          assistant.bridge_url("/health") == "http://127.0.0.1:9999/health",
          assistant.bridge_url("/health"))
    point_at("http://127.0.0.1:8901/")
    check("a trailing slash does not double up",
          assistant.bridge_url("/job/1") == "http://127.0.0.1:8901/job/1",
          assistant.bridge_url("/job/1"))
    point_at(BRIDGE_URL)


def test_port_is_free_after():
    section("the fake bridge let its port go")
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    print("Forge add-on Phase 6 (Assistant) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    fake = FakeBridge(PORT)
    note("fake bridge on %s" % BRIDGE_URL)
    try:
        props = test_registration()
        if props is None:
            raise AssertionError("no assistant props; the rest needs them")
        test_prefs_and_urls()
        test_context_is_collected()
        test_send_round_trip(props, fake)
        test_log_is_trimmed(props, fake)
        test_new_conversation(props, fake)
        test_health(props, fake)
        test_bridge_error_is_shown(props, fake)
        test_busy_is_reported(props, fake)
        test_unreachable_bridge_says_exactly_the_right_thing(props)
        test_panel_draw(props, fake)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        fake.stop()
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
