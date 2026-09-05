"""Headless add-on tests for Phase 6b (Flows).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_flows.py -- --service http://127.0.0.1:8765

The socket port is **9884** (one above the `partforge_open` suite's 9883), never
9876 — a live Blender session owns that one.

What is actually being proved:

1. `flow_list` finds the repo's starter flow with its description and params;
2. `flow_run` executes a small inline flow made of Blender steps, in order;
3. `{{param}}` substitution reaches the command as the *typed* value, and
   `{{steps.N.result.field}}` carries one step's output into the next;
4. a failing step names itself in the error rather than saying "flow failed";
5. the additive `load_meshes` shape (`/segment` segments + a `plate` object)
   loads and positions objects, which is what makes the starter flow's second
   step a plain reference;
6. the panel wiring — properties, operators, and every `.prop(fl, ...)` the
   Flows box draws.

The geometry service is used **read-only** and only if it happens to be
listening: one `/parse_params` call, which builds no geometry and writes
nothing.  With no service running that section is skipped and says so.
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
FLOWS_DIR = os.path.join(REPO_ROOT, "flows")
STARTER = "segment-into-4"

PORT = 9884  # not 9876..9883 (live session + phases 2-7)
DEFAULT_SERVICE = "http://127.0.0.1:8765"

#: A tiny script whose PARAMS block is all /parse_params needs.
STUB_SCRIPT = '''"""Stub part for the flows tests."""

PARAMS = {
    "width": {"value": 20.0, "unit": "mm", "min": 5.0, "max": 80.0, "step": 0.5,
              "description": "Width"},
}


def build(p):
    return None
'''

#: Two triangles, in millimetres, in the shape /segment returns them.
TRI = {
    "vertices": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]],
    "faces": [[0, 1, 2]],
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
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["service_url"] = url
    forge_prefs._FALLBACK.service_url = url
    entry = bpy.context.preferences.addons.get("forge")
    if entry is not None and entry.preferences is not None:
        try:
            entry.preferences.service_url = url
        except (AttributeError, TypeError):
            pass


def point_flows_at(directory):
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["forge_flows_dir"] = directory
    forge_prefs._FALLBACK.forge_flows_dir = directory
    entry = bpy.context.preferences.addons.get("forge")
    if entry is not None and entry.preferences is not None:
        try:
            entry.preferences.forge_flows_dir = directory
        except (AttributeError, TypeError):
            pass


def service_is_up(url):
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url.rstrip("/") + "/health", timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


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


# --- tests ------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import flows, registry

    check("flow_list is a protocol command", registry.has_command("flow_list"))
    check("flow_run is a protocol command", registry.has_command("flow_run"))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "load_meshes", "partforge_open",
               "rigforge_tag", "export_stl")),
          str(len(registry.command_names())))

    props = flows.get_props(bpy.context)
    if not check("scene.forge_flows exists", props is not None):
        return None
    for name in ("flows", "params", "selected", "status", "status_is_error",
                 "summary", "busy", "loaded"):
        check("the flows props carry %s" % name, hasattr(props, name))
    for name in ("flow_refresh", "flow_select", "flow_run"):
        check("forge.%s is registered" % name, hasattr(bpy.ops.forge, name))

    from forge.prefs import DEFAULTS

    check("the flows folder is an add-on preference",
          "forge_flows_dir" in DEFAULTS, str(sorted(DEFAULTS)))
    check("and it defaults to the repo's flows/ folder",
          os.path.normcase(str(DEFAULTS.get("forge_flows_dir") or "").rstrip("\\/"))
          == os.path.normcase(FLOWS_DIR),
          "%r vs %r" % (DEFAULTS.get("forge_flows_dir"), FLOWS_DIR))
    return props


def test_flow_list_sees_the_starter_flow():
    section("flow_list")
    reply = _roundtrip({"type": "flow_list", "params": {}})
    if not check("the command succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply.get("result") or {}
    names = [entry.get("name") for entry in result.get("flows") or []]
    check("the repo's flows folder is what it read",
          os.path.normcase(str(result.get("dir") or "").rstrip("\\/"))
          == os.path.normcase(FLOWS_DIR), str(result.get("dir")))
    if not check("the starter flow is listed", STARTER in names, str(names)):
        return
    entry = [e for e in result["flows"] if e.get("name") == STARTER][0]
    check("with a description an artist can read",
          len(str(entry.get("description") or "")) > 30, str(entry.get("description")))
    check("and with its parameters", set(entry.get("params") or {}) >=
          {"script_path", "wedges", "joint_type", "joint_tolerance", "collection"},
          str(sorted(entry.get("params") or {})))
    check("every parameter declares a value",
          all("value" in spec for spec in (entry.get("params") or {}).values()),
          str(entry.get("params")))
    check("it has two steps", entry.get("steps") == 2, str(entry.get("steps")))
    check("and none of them is broken", not entry.get("error"), str(entry.get("error")))


def test_the_starter_flow_chains_segment_into_blender():
    section("the starter flow's wiring")
    from forge.tools import flows

    doc = flows.read_flow(STARTER)
    steps = doc["steps"]
    check("step 1 asks the geometry service to cut the part",
          steps[0]["kind"] == "service" and steps[0]["op"] == "/segment",
          str(steps[0].get("op")))
    check("with include_mesh true, or nothing would come back to show",
          steps[0]["args"].get("include_mesh") is True, str(steps[0]["args"]))
    check("the wedge count is the parameter, not a hard-coded 4",
          steps[0]["args"]["mode"] == {"radial": "{{wedges}}"},
          str(steps[0]["args"].get("mode")))
    check("step 2 loads the pieces into Blender",
          steps[1]["kind"] == "blender" and steps[1]["op"] == "load_meshes",
          str(steps[1].get("op")))
    check("and it reads step 1's own result",
          steps[1]["args"]["meshes"] == "{{steps.0.result.segments}}"
          and steps[1]["args"]["plate"] == "{{steps.0.result.plate}}",
          str(steps[1]["args"]))
    check("both steps are labelled for a human",
          all(len(str(step.get("label") or "")) > 8 for step in steps),
          str([step.get("label") for step in steps]))

    # The typed-value rule is what makes {{wedges}} a number in the request.
    resolved = flows.substitute(steps[0]["args"], flows.resolve_params(doc, {"wedges": 6}), [])
    check("{{wedges}} arrives as a number, not a string",
          resolved["mode"] == {"radial": 6}, str(resolved.get("mode")))
    check("and an override survives round-tripping through the panel's strings",
          flows.resolve_params(doc, {"wedges": "6"})["wedges"] == 6,
          str(flows.resolve_params(doc, {"wedges": "6"})["wedges"]))


def test_flow_run_executes_blender_steps_in_order():
    section("flow_run with an inline flow")
    for name in ("FlowCubeA", "FlowCubeB"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    inline = {
        "name": "inline-test",
        "description": "Load a triangle, then rename it.",
        "params": {"first": {"value": "FlowCubeA"},
                   "second": {"value": "FlowCubeB"}},
        "steps": [
            {"kind": "blender", "op": "load_mesh", "label": "Load the triangle",
             "args": {"name": "{{first}}", "vertices": TRI["vertices"],
                      "faces": TRI["faces"], "replace": True}},
            {"kind": "blender", "op": "rename_object", "label": "Rename it",
             "args": {"name": "{{steps.0.result.object}}", "new_name": "{{second}}"}},
            {"kind": "blender", "op": "get_scene_info", "label": "Look at the scene",
             "args": {}},
        ],
    }
    reply = _roundtrip({"type": "flow_run", "params": {"flow": inline}})
    if not check("the flow ran", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return
    result = reply.get("result") or {}
    steps = result.get("steps") or []
    check("all three steps are reported", len(steps) == 3, str(len(steps)))
    check("in order, with their labels",
          [step.get("label") for step in steps]
          == ["Load the triangle", "Rename it", "Look at the scene"],
          str([step.get("label") for step in steps]))
    check("each carries kind, op, ok and a brief",
          all({"kind", "op", "ok", "brief"} <= set(step) for step in steps),
          str(steps[:1]))
    check("every step succeeded", all(step.get("ok") for step in steps), str(steps))
    check("the briefs say something about the result",
          all(str(step.get("brief") or "").strip() for step in steps),
          str([step.get("brief") for step in steps]))
    check("{{first}} put the object in the scene under that name",
          "FlowCubeB" in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:8]))
    check("which means step 2 read step 1's result",
          "FlowCubeA" not in bpy.data.objects)
    check("the report names the flow and how long it took",
          result.get("flow") == "inline-test" and isinstance(result.get("duration_ms"), int),
          str(result.get("duration_ms")))
    check("and echoes the parameters it ran with",
          result.get("params") == {"first": "FlowCubeA", "second": "FlowCubeB"},
          str(result.get("params")))


def test_parameter_overrides_reach_the_command():
    section("{{param}} overrides")
    inline = {
        "name": "inline-override",
        "params": {"who": {"value": "Unused"}},
        "steps": [{"kind": "blender", "op": "load_mesh", "label": "Load it",
                   "args": {"name": "{{who}}", "vertices": TRI["vertices"],
                            "faces": TRI["faces"], "replace": True}}],
    }
    reply = _roundtrip({"type": "flow_run",
                        "params": {"flow": inline, "params": {"who": "FlowOverride"}}})
    check("the override won over the declared default",
          reply.get("status") == "success" and "FlowOverride" in bpy.data.objects,
          str(reply.get("message"))[:300])

    reply = _roundtrip({"type": "flow_run",
                        "params": {"flow": inline, "params": {"nope": 1}}})
    check("an unknown parameter is refused by name",
          reply.get("status") == "error" and "nope" in str(reply.get("message")),
          str(reply.get("message"))[:300])


def test_a_failing_step_names_itself():
    section("fail-fast")
    inline = {
        "name": "inline-broken",
        "params": {},
        "steps": [
            {"kind": "blender", "op": "get_scene_info", "label": "Look first",
             "args": {}},
            {"kind": "blender", "op": "select_object", "label": "Select the missing cup",
             "args": {"name": "NoSuchObjectAnywhere"}},
            {"kind": "blender", "op": "get_scene_info", "label": "Never reached",
             "args": {}},
        ],
    }
    reply = _roundtrip({"type": "flow_run", "params": {"flow": inline}})
    message = str(reply.get("message") or "")
    check("the flow failed", reply.get("status") == "error", message[:200])
    check("the error names the step that broke",
          "Select the missing cup" in message, message[:400])
    check("and says where in the flow it was",
          "step 2 of 3" in message, message[:400])
    check("it also says what had already run",
          "Look first" in message, message[:400])
    check("the third step never ran", "Never reached" not in message.split("done first")[-1],
          message[:400])

    bad_op = {"name": "x", "params": {},
              "steps": [{"kind": "blender", "op": "definitely_not_a_command", "args": {}}]}
    reply = _roundtrip({"type": "flow_run", "params": {"flow": bad_op}})
    check("an unknown command is refused before anything runs",
          reply.get("status") == "error"
          and "does not exist" in str(reply.get("message")),
          str(reply.get("message"))[:300])

    bad_endpoint = {"name": "x", "params": {},
                    "steps": [{"kind": "service", "op": "/nope", "args": {}}]}
    reply = _roundtrip({"type": "flow_run", "params": {"flow": bad_endpoint}})
    check("so is an unknown service endpoint",
          reply.get("status") == "error"
          and "does not know" in str(reply.get("message")),
          str(reply.get("message"))[:300])

    reply = _roundtrip({"type": "flow_run", "params": {"name": "no-such-flow-here"}})
    check("and a flow that is not there says what is",
          reply.get("status") == "error" and STARTER in str(reply.get("message")),
          str(reply.get("message"))[:300])


def test_segment_shaped_meshes_load_and_are_placed():
    section("load_meshes takes /segment's own shape (the starter flow's step 2)")
    for name in ("seg_00", "seg_01"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    segment_result = {
        "segments": [
            {"name": "seg_00", "kind": "segment", "mesh": TRI},
            {"name": "seg_01", "kind": "segment", "mesh": TRI},
        ],
        "plate": {"items": [
            {"name": "seg_00", "position_mm": [10.0, 20.0, 0.0], "rotate_deg": 0.0},
            {"name": "seg_01", "position_mm": [40.0, 20.0, 0.0], "rotate_deg": 90.0},
        ]},
    }
    inline = {
        "name": "inline-segments",
        "params": {},
        "steps": [
            {"kind": "blender", "op": "execute_python", "label": "Stand in for /segment",
             "args": {"code": "result = %r" % (json.dumps(segment_result),)}},
            {"kind": "blender", "op": "load_meshes", "label": "Lay them out",
             "args": {"meshes": [dict(entry) for entry in segment_result["segments"]],
                      "plate": segment_result["plate"], "replace": True}},
        ],
    }
    reply = _roundtrip({"type": "flow_run", "params": {"flow": inline}})
    if not check("the flow ran", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return
    check("both segments became objects",
          "seg_00" in bpy.data.objects and "seg_01" in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:8]))
    first = bpy.data.objects.get("seg_00")
    if first is not None:
        # position_mm is millimetres; the add-on works in metres.
        check("and each landed at its plate position, in metres",
              abs(first.location.x - 0.010) < 1e-5 and abs(first.location.y - 0.020) < 1e-5,
              str(tuple(round(v, 5) for v in first.location)))
    second = bpy.data.objects.get("seg_01")
    if second is not None:
        check("with the plate's rotation applied",
              abs(round(second.rotation_euler.z, 3) - 1.571) < 0.01,
              str(round(second.rotation_euler.z, 3)))

    # And the honest error when someone forgets include_mesh.
    from forge.tools import registry

    status, _result, message = registry.dispatch(
        "load_meshes", {"meshes": [{"name": "seg_00", "kind": "segment"}]})
    check("a segment with no mesh says to ask for include_mesh",
          status == "error" and "include_mesh" in message, message[:300])


def test_one_read_only_service_step(service_url):
    section("a service step against the live geometry service (read-only)")
    if not service_is_up(service_url):
        note("no geometry service on %s - service step skipped" % service_url)
        note("(that is a fact about this machine, not a failure)")
        return
    point_at(service_url)

    handle, path = tempfile.mkstemp(suffix=".py", prefix="forge_flow_")
    os.close(handle)
    with open(path, "w", encoding="utf-8") as script:
        script.write(STUB_SCRIPT)
    try:
        inline = {
            "name": "inline-service",
            "params": {"script_path": {"value": path}},
            "steps": [{"kind": "service", "op": "/parse_params",
                       "label": "Ask the service what the sliders are",
                       "args": {"script_path": "{{script_path}}"}}],
        }
        reply = _roundtrip({"type": "flow_run", "params": {"flow": inline}})
        if not check("the service step ran", reply.get("status") == "success",
                     str(reply.get("message"))[:600]):
            return
        step = (reply.get("result") or {}).get("steps", [{}])[0]
        check("it is reported as a service step",
              step.get("kind") == "service" and step.get("op") == "/parse_params",
              str(step))
        check("and the brief mentions the parameters it found",
              "width" in str(step.get("brief")), str(step.get("brief")))
        note("script_path was read from disk and sent as `script` - "
             "the flow file carries no copy of the part")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_panel_wiring(props):
    section("the Flows box")
    import re

    from forge.ui import panels

    cls = getattr(bpy.types, "VIEW3D_PT_forge_flows", None)
    if check("the Flows panel class is registered", cls is not None):
        check("it lives in the Forge sidebar tab", cls.bl_category == "Forge",
              str(cls.bl_category))
        check("it is a top-level box, not a PartForge sub-panel",
              not getattr(cls, "bl_parent_id", ""),
              str(getattr(cls, "bl_parent_id", "")))

    order = [c.__name__ for c in panels._CLASSES]
    check("and it is registered after PartForge",
          order.index("VIEW3D_PT_forge_flows") > order.index("VIEW3D_PT_forge_partforge"),
          str(order))
    check("and before RigForge",
          order.index("VIEW3D_PT_forge_flows") < order.index("VIEW3D_PT_forge_rigforge"),
          str(order))

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_flows")[1].split("\nclass ")[0]
    used = sorted(set(re.findall(r'\.prop\(fl,\s*"([a-z_]+)"', body)))
    unknown = [name for name in used if not hasattr(props, name)]
    check("every fl property the Flows box draws exists", not unknown, str(unknown))
    check("it binds its state to `fl`, not another panel's name",
          not re.search(r'\.prop\((props|rf|ra|chat),\s*"', body),
          "the Flows panel must bind to `fl`")

    operators = sorted(set(re.findall(r'\.operator\(\s*"(forge\.flow_[a-z_]+)"', body)))
    for name in ("forge.flow_refresh", "forge.flow_select", "forge.flow_run"):
        check("the box offers %s" % name, name in operators, str(operators))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every button maps to a registered operator", not missing, str(missing))

    # the operators themselves, run for real
    result = bpy.ops.forge.flow_refresh()
    check("Refresh finished", "FINISHED" in result, str(result))
    names = [entry.name for entry in props.flows]
    check("and it listed the starter flow", STARTER in names, str(names))
    check("the status line says where it read from",
          "flow" in props.status.lower(), props.status)

    result = bpy.ops.forge.flow_select(name=STARTER)
    check("Select finished", "FINISHED" in result, str(result))
    check("the selected flow's parameters are editable in the panel",
          {item.name for item in props.params} >= {"wedges", "joint_type"},
          str([item.name for item in props.params]))
    values = {item.name: item.value for item in props.params}
    check("with the declared defaults filled in", values.get("wedges") == "4",
          str(values))
    check("and a unit where the flow declares one",
          any(item.unit for item in props.params),
          str([(i.name, i.unit) for i in props.params]))
    labelled = [item.label_text() for item in props.params if item.unit]
    check("the label carries the unit", all("(" in text for text in labelled),
          str(labelled))


def test_panel_run_button(props):
    section("the Run button")
    from forge.tools import flows

    obj = bpy.data.objects.get("PanelRunTriangle")
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)

    directory = tempfile.mkdtemp(prefix="forge_flows_")
    inline = {
        "name": "panel-run",
        "description": "Load one triangle, from the panel.",
        "params": {"object_name": {"value": "PanelRunTriangle"}},
        "steps": [{"kind": "blender", "op": "load_mesh", "label": "Load the triangle",
                   "args": {"name": "{{object_name}}", "vertices": TRI["vertices"],
                            "faces": TRI["faces"], "replace": True}}],
    }
    with open(os.path.join(directory, "panel-run.json"), "w", encoding="utf-8") as handle:
        json.dump(inline, handle)

    point_flows_at(directory)
    try:
        bpy.ops.forge.flow_refresh()
        check("the panel lists a flow from the preference folder",
              [entry.name for entry in props.flows] == ["panel-run"],
              str([entry.name for entry in props.flows]))
        result = bpy.ops.forge.flow_run(name="panel-run")
        check("the Run operator finished", "FINISHED" in result, str(result))
        check("the flow actually did the thing",
              "PanelRunTriangle" in bpy.data.objects,
              str(sorted(o.name for o in bpy.data.objects)[:8]))
        check("the panel is not left busy", props.busy is False, str(props.busy))
        check("the result went into the status line",
              "1 step" in props.status and not props.status_is_error, props.status)
        check("and the steps are summarised underneath",
              "Load the triangle" in props.summary, props.summary)

        # a broken flow file is listed WITH its error, not silently dropped
        with open(os.path.join(directory, "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        bpy.ops.forge.flow_refresh()
        broken = [entry for entry in props.flows if entry.name == "broken"]
        check("a flow file that will not parse is still listed", len(broken) == 1,
              str([e.name for e in props.flows]))
        if broken:
            check("with the reason attached", "JSON" in broken[0].error,
                  broken[0].error)
    finally:
        point_flows_at(FLOWS_DIR)
        for filename in ("panel-run.json", "broken.json"):
            try:
                os.remove(os.path.join(directory, filename))
            except OSError:
                pass
        try:
            os.rmdir(directory)
        except OSError:
            pass
    check("the flows folder preference is back on the repo",
          os.path.normcase(flows.flows_dir().rstrip("\\/"))
          == os.path.normcase(FLOWS_DIR), flows.flows_dir())


def test_assistant_activity_lines():
    section("the Assistant's live activity lines (Phase 6b)")
    from forge.tools import assistant
    from forge.ui import panels

    chat = assistant.get_props(bpy.context)
    if not check("the chat props carry an activity list", hasattr(chat, "activity")):
        return
    assistant.set_activity(chat, [
        {"kind": "tool", "label": "partforge_check: part.py"},
        {"kind": "status", "label": "thinking…"},
        {"kind": "text", "label": "Done - I cut it into 4 wedges"},
    ])
    check("the bridge's activity becomes drawable lines", len(chat.activity) == 3,
          str(len(chat.activity)))
    check("keeping kind and label", chat.activity[0].kind == "tool"
          and chat.activity[0].label == "partforge_check: part.py",
          str((chat.activity[0].kind, chat.activity[0].label)))

    assistant.set_activity(chat, [{"kind": "tool", "label": "step %d" % i}
                                  for i in range(20)])
    check("only the last few lines are kept for the sidebar",
          len(chat.activity) == assistant.ACTIVITY_LINES, str(len(chat.activity)))
    check("and they are the newest ones",
          chat.activity[-1].label == "step 19", chat.activity[-1].label)
    check("every activity kind has an icon",
          set(assistant.ACTIVITY_ICONS) >= {"tool", "text", "status"},
          str(sorted(assistant.ACTIVITY_ICONS)))

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_assistant")[1].split("\nclass ")[0]
    check("the Assistant panel draws them under the busy indicator",
          "chat.activity" in body, "the busy branch must show the activity lines")
    chat.activity.clear()


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
    print("Forge add-on Phase 6b (Flows) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    service_url = argv_value("--service", DEFAULT_SERVICE)

    enable_addon()
    point_flows_at(FLOWS_DIR)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        props = test_registration()
        if props is None:
            raise AssertionError("no flows props; the rest needs them")
        test_flow_list_sees_the_starter_flow()
        test_the_starter_flow_chains_segment_into_blender()
        test_flow_run_executes_blender_steps_in_order()
        test_parameter_overrides_reach_the_command()
        test_a_failing_step_names_itself()
        test_segment_shaped_meshes_load_and_are_placed()
        test_one_read_only_service_step(service_url)
        test_panel_wiring(props)
        test_panel_run_button(props)
        test_assistant_activity_lines()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
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
