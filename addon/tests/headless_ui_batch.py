"""Headless add-on tests for the trust-and-navigation UI batch.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_ui_batch.py

The socket port is **9888** — 9876 belongs to the artist's live session and
9884/9886/9887 to the other suites.  Nothing here touches a live service: the
geometry service, the assistant bridge and their ports are replaced by tiny
stdlib HTTP servers on ephemeral ports for the duration of the run.

What is actually being proved, item by item:

1. **undo checkpoints** — a state-changing command pushes a named step and
   ``ed.undo`` really takes it back; read-only commands push nothing; a Blender
   that refuses to push says so once and keeps working;
2. **health row + Start services** — the four rows, both healths parsed off real
   (fake) servers, a dead port reported as down rather than as silence;
3. **quick-action chips** — the three canned sentences, sent through the one
   send path;
4. **empty-state guidance** — every box that can be empty draws its sentence.
   The panels are *executed* against a recording layout, so a typo in a draw
   call fails here rather than in the sidebar;
5. **message queue + cost footer** — a queued reply lands, the session total is
   read off the bridge and formatted;
6. **full-reply viewer** — long replies are detected as truncated and the popup
   operator renders them;
7. **imported models** — ``check_model`` / ``segment_model`` shape their requests
   the way the Phase 6d contract says, against a fake service, and their answers
   land in the Print Checks rows and the viewport;
8. **flow editor** — a copy of the starter flow is edited, reordered, saved and
   read back off disk.
"""

import json
import os
import shutil
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
FLOWS_DIR = os.path.join(REPO_ROOT, "flows")
STARTER = "segment-into-4"

PORT = 9888  # not 9876 (live session) and not 9884/9886/9887 (other suites)

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


# ---------------------------------------------------------------------------
# fakes: a geometry service and an assistant bridge, on ephemeral ports
# ---------------------------------------------------------------------------

#: One canned /check_mesh answer, in the shape docs/architecture.md promises.
CHECK_MESH_REPLY = {
    "overall": "warn",
    "checks": [
        {"name": "bed_fit", "status": "pass", "details": "Fits the bed.",
         "data": {"suggested_segmentation": None}},
        {"name": "watertight", "status": "pass", "details": "Closed mesh.",
         "data": {"solid_is_valid": None, "mesh_is_watertight": True}},
        {"name": "min_wall", "status": "warn", "details": "Thin around the rim.",
         "data": {"min_mm": 0.7}},
    ],
    "printer": {"name": "Fake Centauri", "bed": {"x": 256, "y": 256, "z": 256}},
    "stats": {"vertex_count": 8, "face_count": 6, "bounding_box_mm": [20, 20, 20],
              "watertight": True},
}

#: And one /segment_mesh answer with two pieces and a plate.
SEGMENT_MESH_REPLY = {
    "mode": {"kind": "radial", "count": 2},
    "joint": {"type": "dovetail", "tolerance": 0.2},
    "cuts": 1,
    "segments": [
        {"name": "piece_a", "kind": "segment",
         "stats": {"vertex_count": 3, "face_count": 1},
         "oriented_bbox_mm": [10, 10, 1],
         "mesh": {"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0]], "faces": [[0, 1, 2]]}},
        {"name": "piece_b", "kind": "segment",
         "stats": {"vertex_count": 3, "face_count": 1},
         "oriented_bbox_mm": [10, 10, 1],
         "mesh": {"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0]], "faces": [[0, 1, 2]]}},
    ],
    "plate": {"fits": True,
              "items": [{"name": "piece_a", "position_mm": [0, 0, 0], "rotate_deg": 0},
                        {"name": "piece_b", "position_mm": [30, 0, 0], "rotate_deg": 0}]},
}

#: What the fake service should say to the next mesh request. Flipped by the
#: repair test so the "your model has holes" path is exercised for real.
SERVICE_MODE = {"watertight": True}


class _FakeService(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok", "build123d": "fake-1.0"})
            return
        self._send(404, {"error": "no"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0].rstrip("/")
        _FakeService.requests.append((path, body))

        if not SERVICE_MODE.get("watertight", True):
            self._send(400, {"error": "This mesh is not watertight, so it cannot be "
                                      "made into a solid. Repair it first (voxel "
                                      "remesh in Blender), then try again."})
            return
        if path == "/check_mesh":
            self._send(200, dict(CHECK_MESH_REPLY))
            return
        if path == "/segment_mesh":
            self._send(200, dict(SEGMENT_MESH_REPLY))
            return
        self._send(404, {"error": "unknown endpoint %s" % path})


#: The fake bridge's script for one exchange: the first /job poll says the
#: message is queued behind another one, the second says it is done.
BRIDGE_STATE = {"polls": 0, "cost": 0.0, "auth_error": False, "found": True}


class _FakeBridge(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    asks = []

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            cli = {"found": True, "path": "C:/fake/claude.exe", "version": "9.9.9"}
            if not BRIDGE_STATE.get("found", True):
                cli = {"found": False, "path": None, "hint": "The Claude CLI was not found."}
            self._send(200, {
                "status": "ok",
                "claude_cli": cli,
                "busy": False,
                "queued": False,
                "session_cost_usd": BRIDGE_STATE["cost"],
                "last_auth_error": bool(BRIDGE_STATE.get("auth_error")),
            })
            return
        if path.startswith("/job/"):
            BRIDGE_STATE["polls"] += 1
            if BRIDGE_STATE["polls"] < 2:
                self._send(200, {"job_id": "j1", "state": "queued", "activity": []})
                return
            BRIDGE_STATE["cost"] = 0.42
            self._send(200, {
                "job_id": "j1", "state": "done",
                "reply": "Done - I checked the part and it fits.",
                "activity": [{"kind": "tool", "label": "partforge_check: part.py"}],
                "cost_usd": 0.17, "duration_ms": 4200,
                "session_cost_usd": 0.42,
            })
            return
        self._send(404, {"error": "no"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/ask":
            _FakeBridge.asks.append(body)
            BRIDGE_STATE["polls"] = 0
            self._send(200, {"job_id": "j1", "state": "queued", "queued": True})
            return
        if path == "/new":
            BRIDGE_STATE["cost"] = 0.0
            self._send(200, {"status": "ok", "session": None})
            return
        self._send(404, {"error": "no"})


def start_fake(handler):
    """A threaded HTTP server on an ephemeral port. Returns (server, base_url)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


# ---------------------------------------------------------------------------
# a layout that records instead of drawing
# ---------------------------------------------------------------------------

class FakeLayout(object):
    """Enough of ``UILayout`` to run a panel's draw() with nothing on screen.

    Panels are the part of an add-on no headless test usually reaches, which is
    exactly why an empty-state sentence can rot for months.  Executing draw()
    against this catches a bad property name or a missing operator argument at
    test time.
    """

    def __init__(self, sink=None):
        self._sink = sink if sink is not None else {"labels": [], "operators": [],
                                                    "props": []}
        # Everything a draw() assigns to; assignment must simply work.
        self.active = True
        self.alert = False
        self.enabled = True
        self.scale_y = 1.0
        self.scale_x = 1.0
        self.alignment = "EXPAND"
        self.use_property_split = False

    # -- containers ------------------------------------------------------
    def row(self, align=False):
        return FakeLayout(self._sink)

    def column(self, align=False):
        return FakeLayout(self._sink)

    def box(self):
        return FakeLayout(self._sink)

    def split(self, factor=0.5, align=False):
        return FakeLayout(self._sink)

    def separator(self, factor=1.0):
        return None

    # -- content ---------------------------------------------------------
    def label(self, text="", icon="NONE", **kwargs):
        self._sink["labels"].append(str(text))
        return None

    def prop(self, data, name, **kwargs):
        # Touch the property the way Blender would, so a name that does not
        # exist on the group is a failure here too.
        getattr(data, name)
        self._sink["props"].append(name)
        return None

    def operator(self, idname, **kwargs):
        self._sink["operators"].append(str(idname))
        return _FakeOperatorProps()

    def menu(self, *args, **kwargs):
        return None

    def template_list(self, *args, **kwargs):
        return None

    # -- reading back ----------------------------------------------------
    @property
    def sink(self):
        return self._sink

    def text(self):
        return "\n".join(self._sink["labels"])


class _FakeOperatorProps(object):
    """``layout.operator(...)`` returns something you assign arguments onto."""

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)


def draw_panel(panel_cls, context=None):
    """Run one panel's draw() and return the recording layout."""
    layout = FakeLayout()
    shim = type("PanelShim", (object,), {})()
    shim.layout = layout
    panel_cls.draw(shim, context or bpy.context)
    return layout


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def point_pref(name, value):
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS[name] = value
    setattr(forge_prefs._FALLBACK, name, value)
    entry = bpy.context.preferences.addons.get("forge")
    if entry is not None and entry.preferences is not None:
        try:
            setattr(entry.preferences, name, value)
        except (AttributeError, TypeError):
            pass


def _roundtrip(payload, timeout=60.0):
    """Send one socket command and pump the main-thread queue until it replies."""
    from forge import server as forge_server

    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                box["reply"] = json.loads(buffer.split(b"\n")[0].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=talk, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        if forge_server._server is not None:
            forge_server._server.drain()
        time.sleep(0.01)
    thread.join(timeout=2.0)

    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error",
                                "message": "no reply within %.0fs" % timeout}


def make_cube(name="ImportedModel", size_m=0.02):
    """A 20 mm cube standing in for a downloaded model."""
    existing = bpy.data.objects.get(name)
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    mesh = bpy.data.meshes.new(name)
    half = size_m / 2.0
    verts = [(x * half, y * half, z * half)
             for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
             (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


# ---------------------------------------------------------------------------
# 1. undo checkpoints
# ---------------------------------------------------------------------------

def test_undo_checkpoints():
    section("1. undo checkpoints for AI actions")
    from forge.tools import registry

    check("read-only commands push nothing",
          registry.push_undo("ping") is False and registry.push_undo("get_scene_info") is False)
    check("and the read-only list is the documented one",
          {"ping", "get_scene_info", "flow_list"} <= set(registry.READ_ONLY_COMMANDS),
          str(sorted(registry.READ_ONLY_COMMANDS)))
    check("the step is named for the command",
          registry.undo_message("remesh") == "Forge: remesh",
          registry.undo_message("remesh"))

    pushed = registry.push_undo("load_mesh")
    if not pushed:
        # The guard did its job: report it loudly, do not fail the suite. A
        # Blender with no undo stack must still run every flow.
        check("undo is unavailable in this build, and the guard reported it",
              registry._UNDO_AVAILABLE is False,
              "push_undo returned False; commands still run")
        note("undo checkpoints are OFF in this Blender - the rest of the item is "
             "not testable here")
        return
    check("a state-changing command can push a checkpoint headless", pushed)

    for name in ("UndoProbe",):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    reply = _roundtrip({"type": "load_mesh", "params": {
        "name": "UndoProbe",
        "vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
        "faces": [[0, 1, 2]], "replace": True}})
    if not check("the command ran", reply.get("status") == "success",
                 str(reply.get("message"))[:200]):
        return
    check("and it made the object", "UndoProbe" in bpy.data.objects)

    try:
        bpy.ops.ed.undo()
    except RuntimeError as exc:
        check("ed.undo is available headless", False, str(exc))
        return
    check("Ctrl+Z takes the assistant's work back off the scene",
          "UndoProbe" not in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:8]))

    check("the panel's Revert button is registered",
          hasattr(bpy.ops.forge, "revert_ai"))


# ---------------------------------------------------------------------------
# 2. health row + start services
# ---------------------------------------------------------------------------

def test_health_row(service_url, bridge_url):
    section("2. health row + Start services")
    from forge.tools import services

    props = services.get_props(bpy.context)
    if not check("scene.forge_services exists", props is not None):
        return
    for name in ("service_state", "bridge_state", "cli_state", "status", "busy"):
        check("the services props carry %s" % name, hasattr(props, name))
    check("forge.services_refresh is registered", hasattr(bpy.ops.forge, "services_refresh"))
    check("forge.services_start is registered", hasattr(bpy.ops.forge, "services_start"))

    check("there are exactly four rows", len(services.rows(props)) == 4,
          str([row[0] for row in services.rows(props)]))
    check("and the command socket is read from this process, not a port probe",
          services.socket_row()[0] == services.UP, str(services.socket_row()))

    result = bpy.ops.forge.services_refresh()
    check("the refresh operator finished", "FINISHED" in result, str(result))
    check("the shape service came back up", props.service_state == services.UP,
          "%s / %s" % (props.service_state, props.service_detail))
    check("with the version in the detail line", "fake-1.0" in props.service_detail,
          props.service_detail)
    check("the assistant came back up", props.bridge_state == services.UP,
          "%s / %s" % (props.bridge_state, props.bridge_detail))
    check("and sign-in is reported from the bridge, never by running the CLI",
          props.cli_state == services.UP and "9.9.9" in props.cli_detail,
          "%s / %s" % (props.cli_state, props.cli_detail))
    check("the status line says everything is running",
          not props.status_is_error and "running" in props.status.lower(), props.status)

    # a signed-out CLI is a warning with the fix in it
    BRIDGE_STATE["auth_error"] = True
    bpy.ops.forge.services_refresh()
    check("a signed-out CLI shows as a warning, not as 'up'",
          props.cli_state == services.WARN, props.cli_state)
    check("and the sentence says how to sign in",
          "/login" in props.cli_detail, props.cli_detail)
    BRIDGE_STATE["auth_error"] = False

    # a dead port is reported, not silently ignored
    point_pref("service_url", "http://127.0.0.1:1")
    bpy.ops.forge.services_refresh()
    check("a service that is not there reads as down",
          props.service_state == services.DOWN, props.service_state)
    check("and the status names what is missing",
          props.status_is_error and "Shapes" in props.status, props.status)
    point_pref("service_url", service_url)
    bpy.ops.forge.services_refresh()

    check("the session cost rode along on the health poll",
          abs(float(_chat().session_cost) - BRIDGE_STATE["cost"]) < 1e-6,
          "%s vs %s" % (_chat().session_cost, BRIDGE_STATE["cost"]))

    from forge.prefs import start_script_path

    check("Start Services runs the repo's own start_forge.ps1",
          os.path.basename(start_script_path() or "") == "start_forge.ps1",
          str(start_script_path()))
    check("and the port probe helper answers about a dead port",
          services.probe_port(1) is False)


def _chat():
    from forge.tools import assistant

    return assistant.get_props(bpy.context)


# ---------------------------------------------------------------------------
# 3 + 5 + 6. chips, queue, cost footer, full-reply viewer
# ---------------------------------------------------------------------------

def test_quick_actions_and_queue():
    section("3/5/6. quick chips, the queue, the cost footer, the full reply")
    from forge.tools import assistant

    chat = assistant.get_props(bpy.context)
    keys = [key for key, _label, _icon, _text in assistant.QUICK_ACTIONS]
    check("there are three chips", keys == ["check", "segment", "export"], str(keys))
    check("Check print sends the approved sentence",
          assistant.quick_action_text("check") ==
          "Run the print checks on the current part and explain anything that "
          "fails in plain words.", assistant.quick_action_text("check"))
    check("Segment to fit sends the approved sentence",
          assistant.quick_action_text("segment") ==
          "Segment the current part so every piece fits my printer bed, and lay "
          "the pieces out.", assistant.quick_action_text("segment"))
    check("Export STL sends the approved sentence",
          assistant.quick_action_text("export") ==
          "Export the current part as an STL to the project's exports folder and "
          "tell me where it is.", assistant.quick_action_text("export"))
    check("forge.assistant_quick is registered", hasattr(bpy.ops.forge, "assistant_quick"))

    chat.log.clear()
    chat.session_cost = 0.0
    _FakeBridge.asks[:] = []
    result = bpy.ops.forge.assistant_quick(action="segment")
    check("pressing a chip sends", "FINISHED" in result, str(result))
    check("the bridge got the canned sentence, not a code",
          _FakeBridge.asks and _FakeBridge.asks[-1].get("message") ==
          assistant.quick_action_text("segment"),
          str(_FakeBridge.asks[-1].get("message") if _FakeBridge.asks else None))
    check("it travelled the normal path, with scene context attached",
          isinstance((_FakeBridge.asks or [{}])[-1].get("context"), dict),
          str((_FakeBridge.asks or [{}])[-1].keys()))
    check("the artist's message went into the log",
          len(chat.log) >= 2 and chat.log[0].role == "you", str(len(chat.log)))
    check("a message that starts life queued still lands as an answer",
          any(entry.role == "forge" and "I checked the part" in entry.text
              for entry in chat.log),
          str([entry.text[:40] for entry in chat.log]))
    check("the panel is not left busy", chat.busy is False)
    check("the session total came off the bridge",
          abs(float(chat.session_cost) - 0.42) < 1e-6, str(chat.session_cost))
    check("and the footer says it in dollars and cents",
          assistant.cost_footer(chat) == "This session: $0.42",
          assistant.cost_footer(chat))
    check("this turn's own cost is still shown separately",
          "$0.17" in chat.last_cost, chat.last_cost)

    check("nothing is charged before the first answer",
          assistant.cost_footer(_blank_props(assistant)) == "")

    # the full-reply viewer
    long_reply = "\n".join("Step %d: press the button and wait." % i for i in range(40))
    check("a long reply is spotted as truncated", assistant.is_truncated(long_reply))
    check("a short one is not", not assistant.is_truncated("All done."))
    entry = assistant.append_turn(chat, "forge", long_reply)
    check("the viewer operator is registered",
          hasattr(bpy.ops.forge, "assistant_show_reply"))
    index = len(chat.log) - 1
    result = bpy.ops.forge.assistant_show_reply(index=index)
    check("and it runs headless without a window", "FINISHED" in result, str(result))
    check("the wrapper keeps every line of the reply",
          len(assistant._wrap_text(entry.text, 96)) >= 40,
          str(len(assistant._wrap_text(entry.text, 96))))

    # the popup's own draw(), against the recording layout: it reads the log
    # again rather than trusting anything stashed at invoke time.
    viewer = type("ViewerShim", (object,), {
        "draw": assistant.FORGE_OT_assistant_show_reply.draw,
        "_entry": assistant.FORGE_OT_assistant_show_reply._entry,
    })()
    viewer.layout = FakeLayout()
    viewer.index = index
    viewer.draw(bpy.context)
    drawn = viewer.layout.text()
    check("the popup draws the whole message, not the first screen of it",
          drawn.count("press the button and wait") == 40,
          str(drawn.count("press the button and wait")))
    check("and says who said it", "Forge said:" in drawn, drawn[:60])

    # what the artist sees while their message waits behind another one
    from forge.ui import panels

    chat.busy = True
    chat.queued = True
    chat.status = "Queued - waiting for the current answer ..."
    try:
        text = " ".join(draw_panel(panels.VIEW3D_PT_forge_assistant).text().split())
        check("a waiting message says so in the panel, not just silently",
              "Queued" in text, text[:200])
        check("and Stop is still offered while it waits",
              "forge.assistant_cancel" in
              draw_panel(panels.VIEW3D_PT_forge_assistant).sink["operators"])
    finally:
        chat.busy = False
        chat.queued = False
        chat.status = ""

    # New Conversation zeroes the footer on this side too
    bpy.ops.forge.assistant_new()
    check("New Conversation resets the session total",
          float(chat.session_cost) == 0.0, str(chat.session_cost))
    chat.log.clear()


class _Blank(object):
    session_cost = 0.0


def _blank_props(assistant):
    return _Blank()


# ---------------------------------------------------------------------------
# 4. empty-state guidance (panels are executed, not grepped)
# ---------------------------------------------------------------------------

def test_empty_states():
    section("4. empty-state guidance")
    from forge.ui import panels

    # An empty scene: no part, no params, no checks, no tags, no chat.
    from forge.tools import assistant, flows, model, partforge, rigforge

    chat = assistant.get_props(bpy.context)
    chat.log.clear()
    chat.status = ""
    pf = partforge.get_props(bpy.context)
    pf.script_path = ""
    pf.params.clear()
    pf.checks.clear()
    pf.segment_summary = ""
    md = model.get_props(bpy.context)
    md.object_name = ""
    md.status = ""
    fl = flows.get_props(bpy.context)
    for obj in list(bpy.context.selected_objects):
        obj.select_set(False)
    bpy.context.view_layer.objects.active = None

    expectations = (
        (panels.VIEW3D_PT_forge_assistant, "Type what you want in your own words"),
        (panels.VIEW3D_PT_forge_partforge, "No part yet"),
        (panels.VIEW3D_PT_forge_parameters, "Sliders appear here once a part is loaded"),
        (panels.VIEW3D_PT_forge_checks, "Press Run Checks after generating a part"),
        (panels.VIEW3D_PT_forge_segments, "Cut a part into printable pieces here"),
        (panels.VIEW3D_PT_forge_rigforge, "Select your sculpt and add a tag to start"),
        (panels.VIEW3D_PT_forge_model, "Import an STL you downloaded"),
    )
    for panel_cls, sentence in expectations:
        try:
            layout = draw_panel(panel_cls)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            check("%s draws when it is empty" % panel_cls.bl_label, False, str(exc))
            continue
        text = " ".join(layout.text().split())
        check("%s says what to do when empty" % panel_cls.bl_label,
              sentence.split(" - ")[0][:32] in text, text[:160])

    # the Flows box with nothing in the folder
    empty_dir = tempfile.mkdtemp(prefix="forge_flows_empty_")
    point_pref("forge_flows_dir", empty_dir)
    try:
        flows.refresh(fl)
        layout = draw_panel(panels.VIEW3D_PT_forge_flows)
        text = " ".join(layout.text().split())
        check("Flows says where saved jobs come from",
              "Saved one-button jobs appear here" in text, text[:160])
    finally:
        point_pref("forge_flows_dir", FLOWS_DIR)
        try:
            os.rmdir(empty_dir)
        except OSError:
            pass

    # and every panel draws with a full scene too - a draw() that only works
    # when empty is not much of a fix.
    make_cube("DrawProbe")
    for panel_cls in (panels.VIEW3D_PT_forge_health, panels.VIEW3D_PT_forge_assistant,
                      panels.VIEW3D_PT_forge_server, panels.VIEW3D_PT_forge_partforge,
                      panels.VIEW3D_PT_forge_parameters, panels.VIEW3D_PT_forge_checks,
                      panels.VIEW3D_PT_forge_segments, panels.VIEW3D_PT_forge_export,
                      panels.VIEW3D_PT_forge_model, panels.VIEW3D_PT_forge_flows,
                      panels.VIEW3D_PT_forge_rigforge, panels.VIEW3D_PT_forge_retopo,
                      panels.VIEW3D_PT_forge_uv, panels.VIEW3D_PT_forge_rig,
                      panels.VIEW3D_PT_forge_cloth, panels.VIEW3D_PT_forge_actions,
                      panels.VIEW3D_PT_forge_godot):
        try:
            draw_panel(panel_cls)
            ok = True
            detail = ""
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            ok, detail = False, str(exc)
        check("%s draws" % panel_cls.bl_idname, ok, detail)

    layout = draw_panel(panels.VIEW3D_PT_forge_health)
    text = layout.text()
    check("the health row names all four things",
          all(word in text for word in ("Shapes", "Assistant", "Blender link", "Sign-in")),
          text[:200])
    check("and offers to start what is down",
          "forge.services_start" in layout.sink["operators"],
          str(layout.sink["operators"]))

    layout = draw_panel(panels.VIEW3D_PT_forge_assistant)
    check("the Assistant box draws the three chips",
          layout.sink["operators"].count("forge.assistant_quick") == 3,
          str(layout.sink["operators"]))
    check("and the Revert button",
          "forge.revert_ai" in layout.sink["operators"],
          str(layout.sink["operators"]))


# ---------------------------------------------------------------------------
# 7. imported models: check_model / segment_model
# ---------------------------------------------------------------------------

def test_check_model_against_the_contract():
    section("7. check_model")
    from forge.tools import partforge, registry

    check("check_model is a protocol command", registry.has_command("check_model"))
    check("segment_model is a protocol command", registry.has_command("segment_model"))

    from forge.tools import flows

    check("both are legal flow steps, so a saved job can use them",
          flows._validate_step({"kind": "blender", "op": "check_model"}, 0) is not None
          and flows._validate_step({"kind": "blender", "op": "segment_model"}, 0)
          is not None)
    check("and so are the two mesh endpoints the MCP server writes into flows",
          {"/check_mesh", "/segment_mesh"} <= set(flows.SERVICE_OPS),
          str(sorted(flows.SERVICE_OPS)))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "load_meshes", "partforge_open", "flow_run",
               "rigforge_tag", "export_stl")),
          "%d commands" % len(registry.command_names()))

    make_cube("ImportedModel")
    _FakeService.requests[:] = []
    reply = _roundtrip({"type": "check_model", "params": {"object": "ImportedModel"}})
    if not check("the command succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply.get("result") or {}

    posts = [entry for entry in _FakeService.requests if entry[0] == "/check_mesh"]
    if not check("it posted to /check_mesh", len(posts) == 1,
                 str([entry[0] for entry in _FakeService.requests])):
        return
    body = posts[0][1]
    mesh = body.get("mesh") or {}
    check("with a mesh, per the Phase 6d contract",
          isinstance(mesh.get("vertices"), list) and isinstance(mesh.get("faces"), list),
          str(sorted(body)))
    check("the cube arrived as 6 faces and 8 corners",
          len(mesh.get("faces") or []) == 6 and len(mesh.get("vertices") or []) == 8,
          "%d faces / %d verts" % (len(mesh.get("faces") or []),
                                   len(mesh.get("vertices") or [])))
    extent = max(abs(value) for vertex in mesh["vertices"] for value in vertex)
    check("scaled to millimetres (a 20 mm cube reaches 10 mm from the middle)",
          abs(extent - 10.0) < 1e-6, str(extent))
    check("and the printer profile rode along", "printer" in body, str(sorted(body)))

    check("the verdict came back", result.get("overall") == "warn", str(result.get("overall")))
    check("with the service's own check rows", len(result.get("checks") or []) == 3,
          str(len(result.get("checks") or [])))
    check("and the mesh size is reported",
          (result.get("mesh") or {}).get("face_count") == 6, str(result.get("mesh")))

    pf = partforge.get_props(bpy.context)
    check("the answers land in the Print Checks panel, not a second one",
          len(pf.checks) == 3 and pf.check_overall == "warn",
          "%d rows / %s" % (len(pf.checks), pf.check_overall))
    check("and the summary names the object that was checked",
          "ImportedModel" in pf.check_summary, pf.check_summary)

    names = [row.name for row in pf.checks]
    check("the watertight row is there for a raw mesh", "watertight" in names, str(names))


def test_segment_model_loads_the_pieces():
    section("7. segment_model")
    from forge.tools import model

    for name in ("piece_a", "piece_b"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    _FakeService.requests[:] = []
    reply = _roundtrip({"type": "segment_model", "params": {
        "object": "ImportedModel",
        "joint": {"type": "pin", "tolerance": 0.25},
        "mode": {"radial": 2},
        "collection": "ModelPieces"}})
    if not check("the command succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply.get("result") or {}

    posts = [entry for entry in _FakeService.requests if entry[0] == "/segment_mesh"]
    if not check("it posted to /segment_mesh", len(posts) == 1,
                 str([entry[0] for entry in _FakeService.requests])):
        return
    body = posts[0][1]
    check("the joint travelled verbatim", body.get("joint") ==
          {"type": "pin", "tolerance": 0.25}, str(body.get("joint")))
    check("so did the cut mode", body.get("mode") == {"radial": 2}, str(body.get("mode")))
    check("and the meshes were asked for, or nothing could be shown",
          body.get("include_mesh") is True, str(body.get("include_mesh")))

    check("both pieces are in the scene",
          "piece_a" in bpy.data.objects and "piece_b" in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:10]))
    check("in the collection that was asked for",
          "ModelPieces" in bpy.data.collections and
          "piece_a" in bpy.data.collections["ModelPieces"].objects,
          str(list(bpy.data.collections.keys())))
    check("laid out on the plate, not stacked at the origin",
          abs(bpy.data.objects["piece_b"].location.x
              - bpy.data.objects["piece_a"].location.x) > 1e-4,
          "%s vs %s" % (bpy.data.objects["piece_a"].location.x,
                        bpy.data.objects["piece_b"].location.x))
    check("the result names the objects it made",
          sorted(result.get("objects") or []) == ["piece_a", "piece_b"],
          str(result.get("objects")))
    check("and reports the pieces without shipping their meshes back",
          len(result.get("segments") or []) == 2 and
          all("mesh" not in entry for entry in result.get("segments") or []),
          str(result.get("segments"))[:160])

    md = model.get_props(bpy.context)
    check("the Model box summarises the cut", "2 segment" in (md.summary or ""),
          md.summary)


def test_a_model_with_holes_says_repair_first():
    section("7. the repair-first path")
    from forge.tools import model

    md = model.get_props(bpy.context)
    make_cube("ImportedModel")
    SERVICE_MODE["watertight"] = False
    try:
        reply = _roundtrip({"type": "check_model", "params": {"object": "ImportedModel"}})
        message = str(reply.get("message") or "")
        check("the service's refusal is an error, not a silent pass",
              reply.get("status") == "error", str(reply)[:200])
        check("the artist is told the mesh is not watertight",
              "watertight" in message.lower(), message[:200])
        check("and told exactly which button fixes it",
              "Voxel Repair" in message, message[:300])

        result = bpy.ops.forge.model_check()
        check("the panel's Check button reports it too",
              "FINISHED" in result and md.status_is_error, str(result) + " " + md.status)
        check("and puts the repair button in front of them",
              md.needs_repair is True, str(md.needs_repair))
    finally:
        SERVICE_MODE["watertight"] = True

    faces_before = len(bpy.data.objects["ImportedModel"].data.polygons)
    md.voxel_size_mm = 4.0
    result = bpy.ops.forge.model_repair()
    check("Voxel Repair runs", "FINISHED" in result, str(result) + " " + md.status)
    check("it rebuilt the surface",
          len(bpy.data.objects["ImportedModel"].data.polygons) != faces_before,
          "%d -> %d" % (faces_before,
                        len(bpy.data.objects["ImportedModel"].data.polygons)))
    check("and the warning is cleared", md.needs_repair is False)


def test_import_scales_millimetres_to_metres():
    section("7. Import Model")
    from forge.tools import model

    md = model.get_props(bpy.context)
    directory = tempfile.mkdtemp(prefix="forge_model_")
    path = os.path.join(directory, "widget.stl")
    try:
        make_cube("ExportSource")
        reply = _roundtrip({"type": "export_stl", "params": {
            "objects": ["ExportSource"], "path": path}})
        if not check("a test STL was written", reply.get("status") == "success"
                     and os.path.isfile(path), str(reply.get("message"))[:200]):
            return

        for name in list(bpy.data.objects.keys()):
            if name.startswith("widget"):
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        md.import_units = "MM"
        result = bpy.ops.forge.model_import(filepath=path)
        check("the import operator finished", "FINISHED" in result,
              str(result) + " " + md.status)
        obj = bpy.data.objects.get(md.object_name)
        if not check("and there is a new object", obj is not None, md.object_name):
            return
        size_mm = max(obj.dimensions) * 1000.0
        check("a 20 mm STL comes in 20 mm across, not 20 metres",
              abs(size_mm - 20.0) < 0.5, "%.3f mm" % size_mm)
        check("the status tells them what to do next",
              "Check imported model" in md.status, md.status)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. flow editor
# ---------------------------------------------------------------------------

def test_flow_editor_round_trip():
    section("8. the flow editor")
    from forge.tools import flows

    fl = flows.get_props(bpy.context)
    for name in ("steps", "editing", "edit_description", "edit_json", "dirty"):
        check("the flows props carry %s" % name, hasattr(fl, name))
    for name in ("flow_step_move", "flow_step_delete", "flow_save", "flow_revert"):
        check("forge.%s is registered" % name, hasattr(bpy.ops.forge, name))

    directory = tempfile.mkdtemp(prefix="forge_flow_edit_")
    source = os.path.join(FLOWS_DIR, STARTER + ".json")
    target = os.path.join(directory, STARTER + ".json")
    try:
        shutil.copyfile(source, target)
        point_pref("forge_flows_dir", directory)
        bpy.ops.forge.flow_refresh()
        bpy.ops.forge.flow_select(name=STARTER)

        check("the editor lists the steps with their labels",
              len(fl.steps) == 2 and len(fl.steps[0].label) > 8,
              str([item.label for item in fl.steps]))
        check("and reads the description off the file",
              len(fl.edit_description) > 20, fl.edit_description)

        original = [item.label for item in fl.steps]
        wedges = [item for item in fl.params if item.name == "wedges"]
        if not check("the starter flow has a wedges parameter", bool(wedges),
                     str([item.name for item in fl.params])):
            return
        wedges[0].value = "6"
        fl.edit_description = "Cut it into six and lay the pieces out."

        result = bpy.ops.forge.flow_step_move(index=0, direction="DOWN")
        check("a step can be moved", "FINISHED" in result, str(result))
        check("and the list shows the new order",
              [item.label for item in fl.steps] == list(reversed(original)),
              str([item.label for item in fl.steps]))
        check("nothing is written until Save", json.load(open(target, encoding="utf-8"))
              ["params"]["wedges"]["value"] == 4, "the file changed too early")
        check("but the panel says there are unsaved changes", fl.dirty is True)

        result = bpy.ops.forge.flow_save()
        check("Save writes", "FINISHED" in result, str(result) + " " + fl.status)
        saved = json.load(open(target, encoding="utf-8"))
        check("the new default is on disk, typed like the old one",
              saved["params"]["wedges"]["value"] == 6
              and isinstance(saved["params"]["wedges"]["value"], int),
              str(saved["params"]["wedges"]))
        check("the description was saved too",
              saved["description"] == "Cut it into six and lay the pieces out.",
              saved["description"])
        check("and the steps kept their new order",
              [step.get("label") for step in saved["steps"]] == list(reversed(original)),
              str([step.get("label") for step in saved["steps"]]))
        check("the flow still validates as a flow",
              flows.validate_flow(saved) is saved)

        # deleting below two steps is refused, with the reason
        bpy.ops.forge.flow_step_delete(index=0)
        check("a step can be deleted from the working copy", len(fl.steps) == 1,
              str(len(fl.steps)))
        result = bpy.ops.forge.flow_save()
        check("but saving a one-step flow is refused", "CANCELLED" in result, str(result))
        check("and says why", "two steps" in fl.status, fl.status)
        check("the file on disk is untouched",
              len(json.load(open(target, encoding="utf-8"))["steps"]) == 2)

        bpy.ops.forge.flow_revert()
        check("Discard puts the steps back", len(fl.steps) == 2, str(len(fl.steps)))
        check("and clears the unsaved flag", fl.dirty is False)

        bpy.ops.forge.flow_refresh()
        bpy.ops.forge.flow_select(name=STARTER)
        check("re-reading the saved flow gives the edited default",
              [item.value for item in fl.params if item.name == "wedges"] == ["6"],
              str([(item.name, item.value) for item in fl.params]))
    finally:
        point_pref("forge_flows_dir", FLOWS_DIR)
        shutil.rmtree(directory, ignore_errors=True)
        try:
            bpy.ops.forge.flow_refresh()
        except RuntimeError:
            pass
    check("the flows folder preference is back on the repo",
          os.path.normcase(flows.flows_dir().rstrip("\\/"))
          == os.path.normcase(FLOWS_DIR), flows.flows_dir())


def test_port_is_free_after():
    section("the socket port let go")
    from forge import server as forge_server

    forge_server.stop_server()
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

def main():
    print("Forge add-on UI-batch headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()

    service, service_url = start_fake(_FakeService)
    bridge, bridge_url = start_fake(_FakeBridge)
    note("fake geometry service on %s" % service_url)
    note("fake assistant bridge on %s" % bridge_url)
    point_pref("service_url", service_url)
    point_pref("assistant_url", bridge_url)
    point_pref("forge_flows_dir", FLOWS_DIR)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        test_undo_checkpoints()
        test_health_row(service_url, bridge_url)
        test_quick_actions_and_queue()
        test_check_model_against_the_contract()
        test_segment_model_loads_the_pieces()
        test_a_model_with_holes_says_repair_first()
        test_import_scales_millimetres_to_metres()
        test_flow_editor_round_trip()
        test_empty_states()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for httpd in (service, bridge):
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
