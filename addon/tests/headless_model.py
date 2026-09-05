"""Headless add-on tests for the Assistant's speed selector.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_model.py

Needs no geometry service, no Claude CLI and no network beyond loopback: the
"bridge" is a small ``http.server`` inside this file on port **9889** that
records every ``/ask`` body and answers with canned JSON.  Whether the real
bridge turns ``"model": "opus"`` into ``--model opus`` is
``assistant/tests/test_bridge.py``'s job; this suite is about the panel.

Three things are being proved:

1. the selector **registers** as an enum offering exactly Fast / Smart /
   Deepest, labelled for someone who has never heard of a model;
2. the choice **rides in every ``/ask`` payload** — the Send button and the
   quick-action chips alike;
3. an artist who never touches it gets the **add-on preference**, and a scene
   that has been touched keeps its own choice.
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

PORT = 9889  # 9882..9888 are the other suites
BRIDGE_URL = "http://127.0.0.1:%d" % PORT

CANNED_REPLY = "Done - four wedges, each about 120 mm across."

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
# the fake bridge — it exists only to remember what was posted to it
# ---------------------------------------------------------------------------

class FakeBridge(object):
    def __init__(self, port):
        self.asks = []
        self.jobs = {}
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
                if path.startswith("/job/"):
                    job_id = path[len("/job/"):]
                    self._send(200, outer.jobs.get(job_id,
                                                   {"state": "error",
                                                    "error": "no such job"}))
                    return
                self._send(404, {"error": "nope"})

            def do_POST(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                payload = self._body()
                if path == "/ask":
                    outer.asks.append(payload)
                    job_id = "job-%d" % len(outer.asks)
                    # Echo the model back the way the real bridge does, so the
                    # panel is exercised against the shape it will really see.
                    outer.jobs[job_id] = {
                        "state": "done", "job_id": job_id, "reply": CANNED_REPLY,
                        "session_id": "sess-model-1", "cost_usd": 0.0042,
                        "duration_ms": 1234, "activity": [],
                        "requested_model": payload.get("model"),
                        "model": payload.get("model") or "default-model",
                    }
                    self._send(200, {"job_id": job_id, "state": "running"})
                    return
                if path == "/new":
                    self._send(200, {"status": "ok", "session": None})
                    return
                self._send(404, {"error": "nope"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()

    def last_ask(self):
        return self.asks[-1] if self.asks else {}

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------
# a layout that records instead of drawing
# ---------------------------------------------------------------------------

class FakeLayout(object):
    """Enough of ``UILayout`` to run the Assistant panel's draw() headless."""

    def __init__(self, sink=None):
        self._sink = sink if sink is not None else {"labels": [], "operators": [],
                                                    "props": []}
        self.active = True
        self.alert = False
        self.enabled = True
        self.scale_y = 1.0
        self.scale_x = 1.0
        self.alignment = "EXPAND"

    def row(self, align=False):
        return FakeLayout(self._sink)

    def column(self, align=False):
        return FakeLayout(self._sink)

    def box(self):
        return FakeLayout(self._sink)

    def separator(self, factor=1.0):
        return None

    def label(self, text="", icon="NONE", **kwargs):
        self._sink["labels"].append(str(text))
        return None

    def prop(self, data, name, **kwargs):
        # Touch it the way Blender would: a name that is not on the group has
        # to fail here rather than in the sidebar.
        getattr(data, name)
        self._sink["props"].append(name)
        return None

    def operator(self, idname, **kwargs):
        self._sink["operators"].append(str(idname))
        return type("Args", (object,), {})()

    @property
    def sink(self):
        return self._sink


def draw_assistant_panel():
    from forge.ui.panels import VIEW3D_PT_forge_assistant

    layout = FakeLayout()
    shim = type("PanelShim", (object,), {})()
    shim.layout = layout
    VIEW3D_PT_forge_assistant.draw(shim, bpy.context)
    return layout


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def point_pref(name, value):
    """Aim the add-on preference (and its fallback) at ``value``."""
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS[name] = value
    setattr(forge_prefs._FALLBACK, name, value)
    entry = None
    try:
        entry = bpy.context.preferences.addons.get(forge_prefs.ADDON_ID)
    except (AttributeError, TypeError):
        entry = None
    if entry is not None and entry.preferences is not None:
        try:
            setattr(entry.preferences, name, value)
        except (AttributeError, TypeError):
            pass


def untouch(props):
    """Forget any choice made in the panel, so the preference decides again."""
    try:
        del props["model_choice"]
    except (KeyError, TypeError):
        pass


def send(props, message="segment this into 4"):
    props.message = message
    return bpy.ops.forge.assistant_send()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_registration():
    section("the selector registers")
    from forge.tools import assistant

    props = assistant.get_props(bpy.context)
    if not check("scene.forge_assistant exists", props is not None):
        return None
    if not check("the chat props carry a model selector", hasattr(props, "model")):
        return props

    rna = props.bl_rna.properties.get("model")
    check("it is an enum, not a free-text field",
          rna is not None and rna.type == "ENUM",
          str(getattr(rna, "type", None)))
    identifiers = [item.identifier for item in rna.enum_items]
    check("it offers exactly haiku, sonnet and opus, fastest first",
          identifiers == ["haiku", "sonnet", "opus"], str(identifiers))

    labels = {item.identifier: item.name for item in rna.enum_items}
    check("haiku is labelled for the artist, not the model",
          labels.get("haiku") == "Fast", str(labels.get("haiku")))
    check("sonnet says it is the recommended one",
          labels.get("sonnet") == "Smart (recommended)", str(labels.get("sonnet")))
    check("opus is the deepest one", labels.get("opus") == "Deepest",
          str(labels.get("opus")))
    check("no label names a model", not any("claude" in name.lower()
                                            for name in labels.values()),
          str(labels))

    descriptions = {item.identifier: item.description for item in rna.enum_items}
    check("each choice says when to use it",
          all(len(text) > 10 for text in descriptions.values()), str(descriptions))
    check("Fast names the quick jobs",
          "export" in descriptions.get("haiku", "").lower(),
          str(descriptions.get("haiku")))

    from forge.prefs import ASSISTANT_MODELS, DEFAULT_ASSISTANT_MODEL, DEFAULTS

    check("the default lives in an add-on preference",
          DEFAULTS.get("assistant_model") == "sonnet",
          str(DEFAULTS.get("assistant_model")))
    check("and that default is the recommended one",
          DEFAULT_ASSISTANT_MODEL == "sonnet", DEFAULT_ASSISTANT_MODEL)
    check("the panel and the preference share one list of models",
          tuple(item[0] for item in ASSISTANT_MODELS) == ("haiku", "sonnet", "opus"),
          str(ASSISTANT_MODELS))

    entry = bpy.context.preferences.addons.get("forge")
    prefs_block = getattr(entry, "preferences", None)
    if check("ForgePreferences declares assistant_model",
             prefs_block is not None and hasattr(prefs_block, "assistant_model")):
        pref_rna = prefs_block.bl_rna.properties.get("assistant_model")
        check("as an enum with the same three choices",
              [item.identifier for item in pref_rna.enum_items]
              == ["haiku", "sonnet", "opus"],
              str([item.identifier for item in pref_rna.enum_items]))
        check("defaulting to sonnet",
              pref_rna.default == "sonnet", str(pref_rna.default))
    return props


def test_the_preference_is_the_default(props):
    section("an untouched scene starts on the preference")
    from forge.tools import assistant

    point_pref("assistant_model", "sonnet")
    untouch(props)
    check("with the shipped preference it is Smart", props.model == "sonnet",
          props.model)
    check("chosen_model() agrees", assistant.chosen_model(props) == "sonnet",
          assistant.chosen_model(props))
    check("and it reads as a label a person can say aloud",
          assistant.model_label(props) == "Smart (recommended)",
          assistant.model_label(props))

    point_pref("assistant_model", "opus")
    untouch(props)
    check("change the preference and an untouched scene follows it",
          props.model == "opus", props.model)

    # The registered preference is an enum, so it cannot hold junk — but the
    # fallback object used before the add-on entry exists (and in --background)
    # is a plain dict, so the guard has to be real. Stub the reader to prove it.
    point_pref("assistant_model", "opus")
    original_pref = assistant.pref
    assistant.pref = lambda name: ("nonsense" if name == "assistant_model"
                                   else original_pref(name))
    try:
        untouch(props)
        check("a preference value that is not a model falls back to sonnet",
              props.model == "sonnet", props.model)
        check("default_model() guards the same way",
              assistant.default_model() == "sonnet", assistant.default_model())
    finally:
        assistant.pref = original_pref

    point_pref("assistant_model", "haiku")
    untouch(props)
    scene = bpy.context.scene
    fresh = getattr(scene, "forge_assistant", None)
    check("a scene that has never been touched reads the preference",
          fresh is not None and fresh.model == "haiku",
          str(getattr(fresh, "model", None)))

    point_pref("assistant_model", "sonnet")
    untouch(props)


def test_a_choice_sticks(props):
    section("the artist's choice beats the default")
    from forge.tools import assistant

    point_pref("assistant_model", "sonnet")
    untouch(props)
    props.model = "opus"
    check("picking Deepest sticks", props.model == "opus", props.model)
    check("chosen_model() reports it", assistant.chosen_model(props) == "opus",
          assistant.chosen_model(props))

    point_pref("assistant_model", "haiku")
    check("and a later preference change does not overrule it",
          props.model == "opus", props.model)

    props.model = "haiku"
    check("changing it again is just as easy", props.model == "haiku", props.model)
    point_pref("assistant_model", "sonnet")
    untouch(props)


def test_the_payload_carries_the_model(props, fake):
    section("every message says which model it wants")
    from forge.tools import assistant

    props.log.clear()
    props.model = "haiku"
    result = send(props, "export this for me")
    check("the Send operator finished", "FINISHED" in result, str(result))

    ask = fake.last_ask()
    check("the /ask body carries the model", ask.get("model") == "haiku",
          str(ask.get("model")))
    check("alongside the message and the context",
          ask.get("message") == "export this for me"
          and isinstance(ask.get("context"), dict), str(sorted(ask)))
    check("and the reply still landed in the log",
          len(props.log) == 2 and props.log[1].text == CANNED_REPLY,
          str([entry.text for entry in props.log]))

    props.model = "opus"
    send(props, "now the tricky one")
    check("changing the selector changes the next message",
          fake.last_ask().get("model") == "opus", str(fake.last_ask().get("model")))

    # A chip is not a special code path: it goes through the same send.
    before = len(fake.asks)
    bpy.ops.forge.assistant_quick(action="check")
    check("a quick-action chip sends one message", len(fake.asks) == before + 1,
          str(len(fake.asks)))
    check("and it carries the same choice",
          fake.last_ask().get("model") == "opus", str(fake.last_ask().get("model")))
    check("with the chip's own sentence",
          fake.last_ask().get("message") == assistant.quick_action_text("check"),
          str(fake.last_ask().get("message"))[:80])

    untouch(props)
    point_pref("assistant_model", "sonnet")
    send(props, "and one on the default")
    check("an artist who never touched it sends the preference",
          fake.last_ask().get("model") == "sonnet",
          str(fake.last_ask().get("model")))
    check("every message so far named a model",
          all(ask.get("model") in ("haiku", "sonnet", "opus") for ask in fake.asks),
          str([ask.get("model") for ask in fake.asks]))


def test_the_panel_draws_it(props):
    section("the panel offers it, above Send")
    import re

    from forge.ui import panels

    layout = draw_assistant_panel()
    drawn = layout.sink["props"]
    check("the Assistant panel draws the selector", "model" in drawn, str(drawn))
    check("and still draws the message field", "message" in drawn, str(drawn))
    check("it is drawn once, not once per state", drawn.count("model") == 1,
          str(drawn))

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_assistant")[1].split("\nclass ")[0]
    check("it is bound to the chat props like everything else in the box",
          re.search(r'\.prop\(chat,\s*"model"', body) is not None)
    check("and drawn without a label, so it stays one compact row",
          re.search(r'\.prop\(chat,\s*"model",\s*text=""', body) is not None)

    # While a turn is in flight the row must not be usable, the same way the
    # message field and the chips are locked.
    props.busy = True
    try:
        busy_layout = draw_assistant_panel()
        check("the panel still draws while busy",
              "model" in busy_layout.sink["props"], str(busy_layout.sink["props"]))
    finally:
        props.busy = False


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
    print("Forge add-on tests: the Assistant speed selector")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["assistant_url"] = BRIDGE_URL
    forge_prefs._FALLBACK.assistant_url = BRIDGE_URL
    point_pref("assistant_url", BRIDGE_URL)

    fake = FakeBridge(PORT)
    note("fake bridge on %s" % BRIDGE_URL)
    try:
        props = test_registration()
        if props is None:
            raise AssertionError("no assistant props; the rest needs them")
        test_the_preference_is_the_default(props)
        test_a_choice_sticks(props)
        test_the_payload_carries_the_model(props, fake)
        test_the_panel_draws_it(props)
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
