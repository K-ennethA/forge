"""Headless add-on test for the additive `partforge_open` socket command.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_partforge_open.py -- --service http://127.0.0.1:8765

`partforge_open` is what the MCP tool `partforge_open_in_panel` calls: it points
the PartForge panel at a script and rebuilds its sliders, so a part the assistant
just wrote arrives in the artist's panel without them typing a path anywhere.

The socket port is **9883** (one above the Assistant suite's 9882), never 9876.
The geometry service is used **read-only** — one `/parse_params` call, which
builds no geometry and writes nothing — and it defaults to the live 8765 because
that is what a real session has running. Everything else runs against a schema
supplied inline, with the service pointed at a dead port, so the panel plumbing
is proved to work even with no service at all.

`--background` has no event loop, so `bpy.app.timers` never fires and the socket
server's main-thread pump never runs. `_roundtrip` drains the queue from the main
thread itself, exactly as the timer would (the same trick `headless_phase2.py`
uses).
"""

import json
import os
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
SAMPLE = os.path.join(REPO_ROOT, "service", "samples", "ring_band.py")

PORT = 9883  # not 9876..9882 (live session + phases 2-6)
DEFAULT_SERVICE = "http://127.0.0.1:8765"
DEAD_SERVICE = "http://127.0.0.1:9884"  # nothing ever listens here

#: A two-parameter script used for the schema-supplied paths. It never reaches
#: build123d — only its PARAMS block matters here.
STUB_SCRIPT = '''"""Stub part for the panel tests."""

PARAMS = {
    "width": {"value": 20.0, "unit": "mm", "min": 5.0, "max": 80.0, "step": 0.5,
              "description": "Width"},
    "hole_count": {"value": 3, "unit": "count", "min": 1, "max": 9, "step": 1,
                   "description": "Number of holes"},
}


def build(p):
    return None
'''

STUB_SCHEMA = {
    "width": {"value": 20.0, "unit": "mm", "min": 5.0, "max": 80.0, "step": 0.5,
              "description": "Width"},
    "hole_count": {"value": 3, "unit": "count", "min": 1, "max": 9, "step": 1,
                   "description": "Number of holes"},
}

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


def argv_value(flag, default):
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    return default


# --- setup ------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def point_at(url):
    """Aim the add-on's service URL preference (or its fallback) at ``url``."""
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["service_url"] = url
    forge_prefs._FALLBACK.service_url = url
    entry = bpy.context.preferences.addons.get("forge")
    if entry is not None and entry.preferences is not None:
        try:
            entry.preferences.service_url = url
        except (AttributeError, TypeError):
            pass


def props():
    return bpy.context.scene.forge_partforge


def service_is_up(url):
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url.rstrip("/") + "/health", timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _roundtrip(payload, timeout=30.0):
    """Send one command and pump the main-thread queue until the reply lands."""
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


def open_script(path, **params):
    body = {"script_path": path}
    body.update(params)
    return _roundtrip({"type": "partforge_open", "params": body})


# --- tests ------------------------------------------------------------------

def test_command_is_registered():
    section("registration")
    from forge.tools import registry

    check("partforge_open is a protocol command", registry.has_command("partforge_open"))
    check("and the older PartForge commands are untouched",
          all(registry.has_command(name) for name in ("load_mesh", "load_meshes",
                                                      "export_stl", "ping")))


def test_open_against_the_live_service(service_url):
    section("partforge_open on service/samples/ring_band.py (live service)")
    point_at(service_url)
    state = props()
    state.script_path = ""

    reply = open_script(SAMPLE)
    if not check("the command succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply.get("result") or {}

    check("it reports the resolved script path",
          os.path.normcase(result.get("script") or "") == os.path.normcase(SAMPLE),
          str(result.get("script")))
    check("it reports a parameter count above zero",
          isinstance(result.get("param_count"), int) and result["param_count"] > 0,
          str(result.get("param_count")))
    check("ring_band has its five parameters", result.get("param_count") == 5,
          str(result.get("param_count")))
    check("the schema came from the service", result.get("schema_source") == "service",
          str(result.get("schema_source")))

    check("the scene props carry the script path",
          os.path.normcase(state.script_path) == os.path.normcase(SAMPLE),
          state.script_path)
    check("the sliders exist on the panel", len(state.params) == 5,
          "%d params" % len(state.params))
    names = [item.name for item in state.params]
    check("named after the script's PARAMS block",
          names == ["outer_diameter", "height", "wall_thickness", "chamfer_edges",
                    "chamfer_size"], str(names))
    check("and the result lists the same names", result.get("params") == names,
          str(result.get("params")))

    by_name = {item.name: item for item in state.params}
    check("a mm parameter became a float slider with its range",
          by_name["outer_diameter"].kind == "FLOAT"
          and abs(by_name["outer_diameter"].float_value - 20.0) < 1e-6
          and by_name["outer_diameter"].has_min and by_name["outer_diameter"].has_max,
          by_name["outer_diameter"].kind)
    check("a bool parameter became a checkbox",
          by_name["chamfer_edges"].kind == "BOOL"
          and by_name["chamfer_edges"].bool_value is True,
          by_name["chamfer_edges"].kind)
    check("the object name follows the script", state.object_name == "ring_band",
          state.object_name)
    check("the status line says what was loaded",
          "5 parameter(s)" in state.status and not state.status_is_error, state.status)


def test_stale_results_are_cleared(service_url, tmpdir):
    section("switching scripts clears the previous part's check rows")
    state = props()
    row = state.checks.add()
    row.name = "bed_fit"
    row.status = "fail"
    row.details = "left over from another part"
    state.check_overall = "fail"
    state.suggested_mode = '{"radial": 4}'
    state.stats = "old stats"

    path = os.path.join(tmpdir, "gizmo-holder", "part.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(STUB_SCRIPT)

    reply = open_script(path, params=STUB_SCHEMA)
    check("the command succeeded", reply.get("status") == "success",
          str(reply.get("message"))[:400])
    check("the stale check rows are gone", len(state.checks) == 0,
          "%d rows" % len(state.checks))
    check("the stale verdict is gone", state.check_overall == "", state.check_overall)
    check("the stale segmentation suggestion is gone", state.suggested_mode == "",
          state.suggested_mode)
    check("the stale stats line is gone", state.stats == "", state.stats)
    check("a generically named script takes the folder's name",
          state.object_name == "gizmo-holder", state.object_name)
    return path


def test_a_supplied_schema_needs_no_service(tmpdir):
    section("schema supplied inline (service pointed at a dead port)")
    point_at(DEAD_SERVICE)
    state = props()

    path = os.path.join(tmpdir, "stub_part.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(STUB_SCRIPT)

    reply = open_script(path, params=STUB_SCHEMA)
    if not check("the command succeeded with no service running",
                 reply.get("status") == "success", str(reply.get("message"))[:400]):
        return path
    result = reply.get("result") or {}
    check("two sliders were built", result.get("param_count") == 2,
          str(result.get("param_count")))
    check("the result says the schema was supplied, not fetched",
          result.get("schema_source") == "supplied", str(result.get("schema_source")))
    by_name = {item.name: item for item in state.params}
    check("a count parameter became an integer slider",
          by_name["hole_count"].kind == "INT" and by_name["hole_count"].int_value == 3,
          by_name["hole_count"].kind)
    return path


def test_keep_values(path):
    section("keep_values keeps what the artist tuned")
    state = props()
    by_name = {item.name: item for item in state.params}
    by_name["hole_count"].int_value = 7

    reply = open_script(path, params=STUB_SCHEMA, keep_values=True)
    check("the command succeeded", reply.get("status") == "success",
          str(reply.get("message"))[:400])
    kept = {item.name: item for item in state.params}
    check("the tuned value survived the reload", kept["hole_count"].int_value == 7,
          str(kept["hole_count"].int_value))

    reply = open_script(path, params=STUB_SCHEMA)
    reset = {item.name: item for item in state.params}
    check("and without keep_values it goes back to the schema default",
          reset["hole_count"].int_value == 3, str(reset["hole_count"].int_value))


def test_an_explicit_object_name_wins(path):
    section("an explicit object name overrides the derived one")
    state = props()
    reply = open_script(path, params=STUB_SCHEMA, object="MagnetHolder")
    check("the command succeeded", reply.get("status") == "success",
          str(reply.get("message"))[:400])
    check("the panel will build into the named object",
          state.object_name == "MagnetHolder", state.object_name)
    check("and the result reports it",
          (reply.get("result") or {}).get("object") == "MagnetHolder",
          str(reply.get("result")))


def test_bad_input_is_a_clean_error(tmpdir):
    section("bad input: an error, and the panel left as it was")
    state = props()
    state.script_path = SAMPLE
    before_path = state.script_path
    before_count = len(state.params)

    missing = _roundtrip({"type": "partforge_open", "params": {}})
    check("no script_path is an error naming the parameter",
          missing.get("status") == "error"
          and "script_path" in (missing.get("message") or ""),
          str(missing.get("message"))[:200])

    nowhere = open_script(os.path.join(tmpdir, "does_not_exist.py"))
    check("a missing file is a plain error, not a traceback",
          nowhere.get("status") == "error"
          and "not found" in (nowhere.get("message") or "").lower()
          and "Traceback" not in (nowhere.get("message") or ""),
          str(nowhere.get("message"))[:200])

    check("the panel still points at the script it had",
          state.script_path == before_path, state.script_path)
    check("and still has its sliders", len(state.params) == before_count,
          "%d params" % len(state.params))


def test_a_dead_service_rolls_the_panel_back(tmpdir):
    section("service down: an actionable error, and no half-open panel")
    point_at(DEAD_SERVICE)
    state = props()
    state.script_path = SAMPLE
    before = state.script_path

    path = os.path.join(tmpdir, "stub_part.py")
    reply = open_script(path)  # no schema supplied: it must ask the service
    message = reply.get("message") or ""
    check("it fails rather than opening an empty panel",
          reply.get("status") == "error", str(reply)[:200])
    check("and the message says the service is not reachable",
          "geometry service" in message.lower(), message[:200])
    check("the panel was left pointing where it was",
          state.script_path == before, state.script_path)


def test_port_is_free_after():
    section("the socket server let its port go")
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


# --- entry point ------------------------------------------------------------

def main():
    print("Forge add-on: partforge_open (generator-first panel handoff)")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    service_url = argv_value("--service", DEFAULT_SERVICE)
    enable_addon()

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    if not check("socket server started on 127.0.0.1:%d" % PORT,
                 forge_server.is_running()):
        sys.exit(1)

    tmpdir = tempfile.mkdtemp(prefix="forge_open_")
    try:
        test_command_is_registered()
        if service_is_up(service_url):
            note("geometry service is up at %s (read-only: one /parse_params)"
                 % service_url)
            test_open_against_the_live_service(service_url)
        else:
            check("the geometry service is reachable at %s" % service_url, False,
                  "start it, or pass --service; the live path is the point of "
                  "this suite")
        test_stale_results_are_cleared(service_url, tmpdir)
        stub = test_a_supplied_schema_needs_no_service(tmpdir)
        test_keep_values(stub)
        test_an_explicit_object_name_wins(stub)
        test_bad_input_is_a_clean_error(tmpdir)
        test_a_dead_service_rolls_the_panel_back(tmpdir)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        forge_server.stop_server()
        try:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
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
