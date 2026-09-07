"""Headless add-on tests for the Phase 2 panels (Print Checks + Segments).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_phase2.py -- --service http://127.0.0.1:8769

It needs a geometry service running at ``--service`` (default
``http://127.0.0.1:8769``, deliberately not the 8765 a real session would use).
Nothing is written into the repo: the oversized test part is generated into a
temp folder and removed on the way out.

Two things about ``--background`` shape this file:

* There is no event loop, so ``bpy.app.timers`` never fires. The PartForge
  operators already run synchronously in background mode, but the socket
  server's main-thread pump does not — so the socket test drains the queue
  itself, from this (the main) thread, exactly as the timer would.
* No window, no viewport, no 3D area. Every code path under test has to work
  without one, which is the point of testing it here.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

import bpy

# --- harness ----------------------------------------------------------------

ADDON_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
SAMPLE = os.path.join(REPO_ROOT, "service", "samples", "ring_band.py")
PRINTER = os.path.join(REPO_ROOT, "templates", "printer.json")

#: A ring far too wide for a 256 mm bed, so bed_fit must fail and suggest radial cuts.
BIG_RING = '''"""Test part: a ring band that cannot fit a 256 mm bed."""

from build123d import *  # noqa: F403

PARAMS = {
    "outer_diameter": {"value": 320.0, "unit": "mm", "min": 4.0, "max": 600.0,
                       "description": "Outer diameter of the band"},
    "height": {"value": 40.0, "unit": "mm", "min": 0.5, "max": 200.0,
               "description": "Height along Z"},
    "wall_thickness": {"value": 8.0, "unit": "mm", "min": 0.4, "max": 50.0,
                       "description": "Radial wall thickness"},
}


def build(p):
    outer = p["outer_diameter"] / 2.0
    inner = outer - p["wall_thickness"]
    if inner <= 0.0:
        raise ValueError("wall_thickness must be less than the outer radius")
    with BuildPart() as band:  # noqa: F405
        with BuildSketch(Plane.XY):  # noqa: F405
            Circle(radius=outer)  # noqa: F405
            Circle(radius=inner, mode=Mode.SUBTRACT)  # noqa: F405
        extrude(amount=p["height"])  # noqa: F405
    return band.part
'''

_RESULTS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + detail) if detail and not condition else ""))
    return bool(condition)


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

def enable_addon(service_url):
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)

    from forge import prefs as forge_prefs

    entry = bpy.context.preferences.addons.get("forge")
    target = entry.preferences if entry is not None and entry.preferences else None
    if target is not None:
        target.service_url = service_url
        target.printer_path = PRINTER if os.path.isfile(PRINTER) else ""
    # ``--factory-startup`` can leave the preferences entry without a prefs
    # instance; the fallback object is what get_prefs() then hands out.
    forge_prefs.DEFAULTS["service_url"] = service_url
    forge_prefs.DEFAULTS["printer_path"] = PRINTER if os.path.isfile(PRINTER) else ""
    forge_prefs._FALLBACK.service_url = service_url
    forge_prefs._FALLBACK.printer_path = forge_prefs.DEFAULTS["printer_path"]

    from forge.prefs import service_url as resolved

    return resolved()


def props():
    return bpy.context.scene.forge_partforge


def run(operator, expect_error=False):
    """Call an operator and return (status_ok, status_text).

    An operator that ``self.report({"ERROR"}, ...)`` and cancels makes ``bpy.ops``
    raise, which is the add-on's existing convention for input it refuses before
    any HTTP call; the message still lands in the panel's status line, and that
    is what the expect_error cases are actually asserting.
    """
    state = props()
    try:
        result = operator()
    except RuntimeError:
        result = {"CANCELLED"}
    ok = "FINISHED" in result and not state.status_is_error
    if expect_error:
        return state.status_is_error, state.status
    if not ok:
        print("     status: %s" % state.status)
    return ok, state.status


# --- tests ------------------------------------------------------------------

def test_service_reachable():
    section("service reachable")
    ok, status = run(bpy.ops.forge.pf_health)
    check("geometry service answers /health", ok, status)
    return ok


def test_checks_on_the_sample():
    section("Print Checks on service/samples/ring_band.py")
    state = props()
    state.script_path = SAMPLE

    ok, status = run(bpy.ops.forge.pf_load_script)
    check("Load Script populated the panel", ok and len(state.params) == 5, status)

    ok, status = run(bpy.ops.forge.pf_check)
    check("Run Checks finished", ok, status)
    check("four checks stored on the scene", len(state.checks) == 4,
          "got %d" % len(state.checks))
    check("overall is pass", state.check_overall == "pass",
          "got %r" % state.check_overall)

    names = [row.name for row in state.checks]
    check("every check is named",
          names == ["bed_fit", "min_wall", "overhangs", "watertight"], str(names))
    check("every check has a status and details",
          all(row.status in ("pass", "warn", "fail") and row.details for row in state.checks))
    check("every check maps to a panel icon",
          all(row.icon() in ("CHECKMARK", "ERROR", "CANCEL") for row in state.checks))
    check("the printer profile is named in the summary",
          "Centauri" in state.check_summary, state.check_summary)


def test_checks_fail_on_an_oversized_part(script_path):
    section("Print Checks on an oversized part")
    # Imported here, like the other addon-internals reaches in this file: the
    # add-on is only on sys.path once bootstrap() has enabled it.
    from forge.tools import partforge

    state = props()
    state.script_path = script_path

    run(bpy.ops.forge.pf_load_script)
    run(bpy.ops.forge.pf_check)

    # The service's own word is stored exactly as it arrived — only what the
    # panel SHOWS is reframed.
    check("overall is fail", state.check_overall == "fail", "got %r" % state.check_overall)
    bed_fit = next((row for row in state.checks if row.name == "bed_fit"), None)
    check("bed_fit failed", bed_fit is not None and bed_fit.status == "fail")
    check("a segmentation mode was suggested",
          state.suggested_mode and json.loads(state.suggested_mode).get("radial", 0) >= 2,
          state.suggested_mode)

    # Bed fit is print planning, not a design constraint. A part that merely
    # prints in pieces is not broken, so nothing about the row says failure.
    check("bed_fit is flagged as a split, not a fault",
          bed_fit is not None and bed_fit.split)
    check("bed_fit does NOT wear the failure icon",
          bed_fit is not None and bed_fit.icon() == "MOD_BOOLEAN",
          bed_fit.icon() if bed_fit else "")
    check("the row's hint says how it prints, not that it is broken",
          bed_fit is not None and bed_fit.hint.startswith("Prints as ")
          and "pieces" in bed_fit.hint and "Segments box" in bed_fit.hint,
          bed_fit.hint if bed_fit else "")
    check("the panel's verdict discounts a split the printer can just cut",
          partforge.design_overall(state) != "fail",
          partforge.design_overall(state))
    check("the Segments box says how it prints instead of showing raw JSON",
          partforge.suggested_split_label(state).startswith("Prints as ")
          and "press Segment" in partforge.suggested_split_label(state),
          partforge.suggested_split_label(state))


def test_radial_dovetail_segment(script_path):
    section("Segments: radial 4, dovetail")
    state = props()
    state.script_path = script_path
    state.segment_mode = "RADIAL"
    state.segment_radial = 4
    state.joint_type = "dovetail"
    state.joint_tolerance = 0.0
    state.segment_collection = ""

    before = set(bpy.data.objects.keys())
    ok, status = run(bpy.ops.forge.pf_segment)
    check("Segment finished", ok, status)

    created = [name for name in bpy.data.objects.keys() if name not in before]
    check("four segment objects landed in the scene", len(created) == 4, str(created))
    check("named after the service's segments",
          sorted(created) == ["segment_%d" % i for i in range(1, 5)], str(sorted(created)))

    objects = [bpy.data.objects[name] for name in sorted(created)]
    check("every segment has geometry", all(len(o.data.vertices) > 0 for o in objects))

    positions = {tuple(round(v, 5) for v in o.location) for o in objects}
    check("each sits at its own plate position", len(positions) == 4,
          "\n     " + "\n     ".join(str(p) for p in sorted(positions)))
    check("all four are laid out on the plate, not stacked at the origin",
          all(abs(o.location.x) + abs(o.location.y) > 0.0 for o in objects))
    check("plate layout is inside a 256 mm bed",
          all(0.0 <= o.location.z <= 0.001 for o in objects),
          str([round(o.location.z, 6) for o in objects]))
    check("each is spun to its packed orientation",
          len({round(o.rotation_euler.z, 6) for o in objects}) == 4,
          str([round(o.rotation_euler.z, 4) for o in objects]))

    check("the summary says what happened",
          "4 segment(s)" in state.segment_summary and "dovetail" in state.segment_summary,
          state.segment_summary)

    # The mesh itself must stay in assembly coordinates; only the object moved.
    ring = objects[0]
    local_min = min(v.co.x for v in ring.data.vertices)
    check("mesh data stayed at the origin (object transform did the work)",
          abs(local_min) < 0.2, "local min x = %.4f m" % local_min)


def test_export_segments(script_path, out_dir):
    section("Export Segments")
    state = props()
    state.script_path = script_path
    state.segment_export_dir = out_dir
    state.segment_basename = "test_ring"
    state.export_format = "stl"

    ok, status = run(bpy.ops.forge.pf_export_segments)
    check("Export Segments finished", ok, status)

    written = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
    stls = [name for name in written if name.endswith(".stl")]
    check("one STL per segment", len(stls) == 4, str(written))
    check("a packed plate 3MF too", "test_ring_plate.3mf" in written, str(written))
    check("every file has content",
          all(os.path.getsize(os.path.join(out_dir, name)) > 0 for name in written))


def test_load_meshes_over_the_socket():
    """The additive `load_meshes` command, driven the way the MCP server drives it."""
    section("socket: load_meshes (bulk, with plate placement)")
    from forge import server as forge_server

    port = 9878  # not 9876: a real Blender session may own that one
    forge_server.start_server(host="127.0.0.1", port=port)
    if not check("server started on 127.0.0.1:%d" % port, forge_server.is_running()):
        return

    payload = {
        "type": "load_meshes",
        "params": {
            "replace": True,
            "meshes": [
                {
                    "name": "bulk_a",
                    "vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]],
                    "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]],
                    "plate": {"position_mm": [5.0, 5.0, 0.0],
                              "pre_rotate_deg": 90.0, "rotate_deg": 0.0},
                },
                {
                    "name": "bulk_b",
                    "vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]],
                    "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]],
                    "plate": {"position_mm": [40.0, 5.0, 0.0],
                              "pre_rotate_deg": 0.0, "rotate_deg": 0.0},
                },
            ],
        },
    }

    reply = _socket_roundtrip(port, payload)
    check("load_meshes succeeded", reply.get("status") == "success", str(reply.get("message")))
    result = reply.get("result") or {}
    check("two objects reported", result.get("count") == 2, str(result))
    check("both exist in the scene",
          "bulk_a" in bpy.data.objects and "bulk_b" in bpy.data.objects)

    if "bulk_a" in bpy.data.objects and "bulk_b" in bpy.data.objects:
        a = bpy.data.objects["bulk_a"]
        b = bpy.data.objects["bulk_b"]
        # a is rotated 90 deg about Z: its rotated min corner is (-0.010, 0) m, so
        # the object has to sit 10 mm further along +X than the plate position.
        check("rotated segment is offset by its rotated bounds",
              abs(a.location.x - 0.015) < 1e-6 and abs(a.location.y - 0.005) < 1e-6,
              str(tuple(round(v, 6) for v in a.location)))
        check("unrotated segment lands exactly on its plate position",
              abs(b.location.x - 0.040) < 1e-6 and abs(b.location.y - 0.005) < 1e-6,
              str(tuple(round(v, 6) for v in b.location)))
        check("rotation was applied about Z only",
              abs(a.rotation_euler.z - 1.5707963) < 1e-5
              and abs(a.rotation_euler.x) < 1e-9,
              str(tuple(round(v, 5) for v in a.rotation_euler)))

    # load_mesh shares its body with load_meshes now; make sure the singular
    # command still behaves exactly as it did before, plate placement included.
    single = _socket_roundtrip(port, {
        "type": "load_mesh",
        "params": {
            "name": "single_a",
            "vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]],
            "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]],
        },
    })
    check("load_mesh still works unchanged",
          single.get("status") == "success"
          and (single.get("result") or {}).get("vertex_count") == 4,
          str(single))
    if "single_a" in bpy.data.objects:
        check("an unplaced mesh stays at the origin",
              tuple(round(v, 6) for v in bpy.data.objects["single_a"].location) == (0.0, 0.0, 0.0))

    unknown = _socket_roundtrip(port, {"type": "load_meshes", "params": {"meshes": []}})
    check("an empty batch is a clean error, not a crash",
          unknown.get("status") == "error" and "empty" in (unknown.get("message") or ""),
          str(unknown))

    forge_server.stop_server()
    check("server stopped and the port is free", not forge_server.is_running())


def _socket_roundtrip(port, payload, timeout=20.0):
    """Send one command and pump the main-thread queue until the reply lands.

    ``--background`` has no event loop, so ``bpy.app.timers`` never runs the
    add-on's pump. The reader thread parks the job on the queue and blocks; this
    loop is the main thread doing what the timer would have done.
    """
    import socket as socketlib
    import threading

    from forge import server as forge_server

    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", port), timeout=timeout)
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
    return box.get("reply") or {"status": "error", "message": "no reply within %.0fs" % timeout}


def test_panels_are_registered_and_reference_real_properties():
    """`draw()` never runs headless, so check what it would touch instead.

    A typo in a `layout.prop(props, "...")` or an operator id only shows up when
    a 3D view draws the panel, which `--background` will never do. Scanning the
    panel source for those two call shapes catches the same class of mistake.
    """
    section("panel wiring")
    import re

    from forge.ui import panels

    for name in ("VIEW3D_PT_forge_checks", "VIEW3D_PT_forge_segments"):
        cls = getattr(bpy.types, name, None)
        check("%s is registered" % name, cls is not None)
        if cls is not None:
            check("%s hangs off the PartForge panel" % name,
                  cls.bl_parent_id == "VIEW3D_PT_forge_partforge")

    source = open(panels.__file__, "r", encoding="utf-8").read()
    state = props()
    unknown = sorted({
        name for name in re.findall(r'\.prop\(props,\s*"([a-z_]+)"', source)
        if not hasattr(state, name)
    })
    check("every panel property exists on the scene props", not unknown, str(unknown))

    operators = sorted(set(re.findall(r'\.operator\("(forge\.[a-z_]+)"', source)))
    missing = [
        name for name in operators
        if not hasattr(getattr(bpy.ops, name.split(".")[0]), name.split(".")[1])
    ]
    check("every panel button maps to a registered operator", not missing, str(missing))
    check("the Phase 2 operators are among them",
          {"forge.pf_check", "forge.pf_segment", "forge.pf_export_segments"} <= set(operators))


def test_error_paths_do_not_raise():
    section("failures land in the status line, not in a traceback")
    state = props()
    state.script_path = ""
    ok, status = run(bpy.ops.forge.pf_check, expect_error=True)
    check("a missing script is reported in the status line", ok and "script" in status.lower(),
          status)

    state.script_path = SAMPLE
    state.segment_mode = "PLANAR"
    state.segment_planar = "not a number"
    ok, status = run(bpy.ops.forge.pf_segment, expect_error=True)
    check("a bad planar height is reported in the status line", ok, status)
    state.segment_mode = "AUTO"
    state.segment_planar = ""


# --- entry point ------------------------------------------------------------

def main():
    service = argv_value("--service", os.environ.get("FORGE_TEST_SERVICE_URL",
                                                     "http://127.0.0.1:8769"))
    print("Forge add-on Phase 2 headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    print("  service %s" % service)

    resolved = enable_addon(service)
    check("add-on enabled and pointed at the test service", resolved == service.rstrip("/"),
          resolved)

    workspace = tempfile.mkdtemp(prefix="forge_addon_test_")
    big_script = os.path.join(workspace, "big_ring.py")
    with open(big_script, "w", encoding="utf-8") as handle:
        handle.write(BIG_RING)
    exports = os.path.join(workspace, "exports")

    try:
        if not test_service_reachable():
            print("\nThe geometry service is not reachable; the rest cannot run.")
        else:
            test_checks_on_the_sample()
            test_checks_fail_on_an_oversized_part(big_script)
            test_radial_dovetail_segment(big_script)
            test_export_segments(big_script, exports)
        test_load_meshes_over_the_socket()
        test_panels_are_registered_and_reference_real_properties()
        test_error_paths_do_not_raise()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    # Blender swallows a plain exit code from --python, so say it out loud too.
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
