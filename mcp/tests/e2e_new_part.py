"""End-to-end proof of the generator-first loop, against everything live.

    mcp\\.venv\\Scripts\\python.exe mcp\\tests\\e2e_new_part.py

This is the whole assistant loop for "make me a small magnet holder", driven by
the same tool functions Claude calls, with nothing faked:

    partforge_new_part  ->  projects/small-magnet-holder/part.py + spec.json
    partforge_open_in_panel  ->  the sliders land in Blender's Forge panel
    partforge_generate  ->  the part is an object in the scene
    partforge_check  ->  the print verdict
    (fails?)  partforge_new_part(overwrite=True)  ->  check again

Round 1 is deliberately a script written the way a model writes one from memory:
a hard-coded thin wall, no `forge_lib`. Round 2 is the reference script from
`service/samples/magnet_holder.py` (the authoring rulebook's own example), which
asks the library for every printable number. Watching the verdict move between
them is the point — the self-correction law in `assistant/system_prompt.md` is
what this file proves is possible.

Not a pytest module (the name does not start with `test_`), because it needs the
real geometry service on 8765 and it launches Blender. It cleans up after itself:
the created project folder is deleted, and the Blender it starts is its own
headless one on port 9885 — never 9876, so a real session is untouched.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mcp"))

from forge_mcp import config, server  # noqa: E402
from forge_mcp.errors import BackendError, ForgeError  # noqa: E402

BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")
PORT = 9885  # not 9876 (a real session), not 9879-9883 (the headless suites)
PART_NAME = "small magnet holder"
SLUG = "small-magnet-holder"

REFERENCE = REPO_ROOT / "service" / "samples" / "magnet_holder.py"

#: Round 1: what a script written from memory looks like. The wall around the
#: pocket is a number typed into the file rather than one asked of the printer
#: profile, which is exactly the mistake docs/part-authoring.md exists to stop.
NAIVE_DRAFT = '''"""A small magnet holder -- first draft, written from memory."""

from build123d import *  # noqa: F403

PARAMS = {
    "magnet_diameter": {"value": 6.0, "unit": "mm", "min": 3.0, "max": 20.0,
                        "step": 0.5, "description": "Diameter of the disc magnet"},
    "magnet_thickness": {"value": 3.0, "unit": "mm", "min": 1.0, "max": 10.0,
                         "step": 0.5, "description": "Thickness of the disc magnet"},
    "wall": {"value": 0.3, "unit": "mm", "min": 0.1, "max": 6.0, "step": 0.1,
             "description": "Wall around and under the magnet pocket"},
}


def build(p):
    wall = p["wall"]
    pocket_r = p["magnet_diameter"] / 2.0
    outer_r = pocket_r + wall
    height = p["magnet_thickness"] + wall
    body = Pos(0, 0, height / 2.0) * Cylinder(radius=outer_r, height=height)  # noqa: F405
    pocket = Pos(0, 0, wall + p["magnet_thickness"] / 2.0) * Cylinder(  # noqa: F405
        radius=pocket_r, height=p["magnet_thickness"] + 1.0
    )
    return body - pocket
'''

#: Round 2, the fallback: the same draft with the one thing the check complained
#: about actually fixed. Used when the reference script cannot build against the
#: geometry service that is running (its `forge_lib` may be older than the
#: sample), because the loop being proved here is the revision, not the helper.
REVISED_DRAFT = NAIVE_DRAFT.replace(
    '"""A small magnet holder -- first draft, written from memory."""',
    '"""A small magnet holder -- revised: the wall carries the printer\'s minimum."""',
).replace(
    '"wall": {"value": 0.3, "unit": "mm", "min": 0.1, "max": 6.0, "step": 0.1,',
    '"wall": {"value": 2.4, "unit": "mm", "min": 1.2, "max": 6.0, "step": 0.1,',
)

_FAILURES = []


def step(title):
    print("\n== %s ==" % title)


def check(label, condition, detail=""):
    ok = bool(condition)
    if not ok:
        _FAILURES.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not ok else ""))
    return ok


def verdict(report):
    """The overall word out of a partforge_check report's first line."""
    head = report.splitlines()[0] if report else ""
    for word in ("PASS", "WARN", "FAIL"):
        if ": %s " % word in head:
            return word
    return "?"


# --- the live backends ------------------------------------------------------

def service_is_up():
    reachable, detail = __import__(
        "forge_mcp.service_client", fromlist=["is_available"]
    ).is_available()
    print("  geometry service %s — %s" % ("UP" if reachable else "DOWN", detail))
    return reachable


def wait_for_port(port, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


HOST_SCRIPT = '''"""Headless Blender host for the E2E: socket server + a main-thread drain loop."""
import os, sys, time

ADDON_DIR = r"{addon_dir}"
PORT = {port}
STOP_FILE = r"{stop_file}"

sys.path.insert(0, ADDON_DIR)
import bpy, addon_utils  # noqa: E402

addon_utils.enable("forge", default_set=True, persistent=False)
from forge import server as forge_server  # noqa: E402

forge_server.start_server(host="127.0.0.1", port=PORT)
print("E2E-HOST-READY", flush=True)

# --background has no event loop, so bpy.app.timers never fires: this loop is
# the main thread doing what the add-on's timer pump would have done.
deadline = time.monotonic() + {life}
while time.monotonic() < deadline and not os.path.exists(STOP_FILE):
    if forge_server._server is not None:
        forge_server._server.drain()
    time.sleep(0.01)

forge_server.stop_server()
print("E2E-HOST-DONE", flush=True)
'''


def start_blender(tmpdir: Path):
    stop_file = tmpdir / "stop"
    host = tmpdir / "e2e_host.py"
    host.write_text(
        HOST_SCRIPT.format(
            addon_dir=str(REPO_ROOT / "addon"),
            port=PORT,
            stop_file=str(stop_file),
            life=900,
        ),
        encoding="utf-8",
    )
    log = open(tmpdir / "blender.log", "w", encoding="utf-8")
    process = subprocess.Popen(
        [str(BLENDER), "--background", "--factory-startup", "--python", str(host)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(REPO_ROOT),
    )
    return process, stop_file, log


# --- the loop ---------------------------------------------------------------

def run_round(label, source, overwrite):
    step(label)
    report = server.partforge_new_part(PART_NAME, source, overwrite=overwrite)
    print(report)
    script = REPO_ROOT / "projects" / SLUG / "part.py"

    print()
    print(server.partforge_open_in_panel(str(script)))
    print()
    print(server.partforge_generate(str(script)))
    print()
    check_report = server.partforge_check(str(script))
    print(check_report)
    return script, check_report


def main() -> int:
    print("Forge end-to-end: the assistant makes a part it has never seen")
    print("  repo:    %s" % REPO_ROOT)
    print("  blender: %s" % BLENDER)

    if not BLENDER.is_file():
        print("  no Blender at that path")
        return 2
    if not service_is_up():
        print("  start the geometry service on 8765 first")
        return 2

    folder = REPO_ROOT / "projects" / SLUG
    if folder.exists():
        print("  refusing to run: %s already exists (this test owns that name)" % folder)
        return 2

    config.BLENDER_PORT = PORT
    config.BLENDER_READ_TIMEOUT = 300.0

    tmpdir = Path(tempfile.mkdtemp(prefix="forge_e2e_"))
    process, stop_file, log = start_blender(tmpdir)
    try:
        step("live backends")
        if not check("headless Blender is listening on %d" % PORT,
                     wait_for_port(PORT)):
            return 1
        print("  " + server.blender_ping())

        # --- round 1: the draft a model writes from memory ------------------
        script, report_1 = run_round(
            "round 1 — first draft (hard-coded 0.3 mm wall, no forge_lib)",
            NAIVE_DRAFT, overwrite=False,
        )
        spec = folder / "spec.json"
        check("part.py was written", script.is_file(), str(script))
        check("spec.json was written alongside it", spec.is_file(), str(spec))
        check("and nothing else was created",
              sorted(p.name for p in folder.iterdir()) == ["part.py", "spec.json"],
              str(sorted(p.name for p in folder.iterdir())))

        scene = server.get_scene_info()
        check("the part is an object in the Blender scene", SLUG in scene,
              scene.splitlines()[0])
        check("the panel is pointed at the same script",
              server.execute_blender_python(
                  "import bpy; print(bpy.context.scene.forge_partforge.script_path)"
              ).strip().lower() == str(script).lower(),
              "panel script path")
        check("the panel has the script's sliders on it",
              server.execute_blender_python(
                  "import bpy; print(len(bpy.context.scene.forge_partforge.params))"
              ).strip() == "3", "slider count")

        first = verdict(report_1)
        check("round 1 produced a real verdict", first in ("PASS", "WARN", "FAIL"),
              first)
        print("\n  round 1 verdict: %s" % first)

        # --- round 2: the revision the loop is supposed to make -------------
        if first == "PASS":
            print("  (round 1 passed; revising anyway, which is the same call)")
        check("the reference script exists to revise towards", REFERENCE.is_file(),
              str(REFERENCE))

        attempts = []
        if REFERENCE.is_file():
            attempts.append(
                ("the forge_lib reference script (service/samples/magnet_holder.py)",
                 REFERENCE.read_text(encoding="utf-8")),
            )
        attempts.append(
            ("a self-contained revision: the wall raised off the printer's minimum",
             REVISED_DRAFT),
        )

        source_2 = report_2 = None
        for label, candidate in attempts:
            try:
                script, report_2 = run_round(
                    "round 2 — revised with %s (partforge_new_part overwrite=True)"
                    % label, candidate, overwrite=True,
                )
            except BackendError as exc:
                print("\n  that revision does not build against the running "
                      "service:\n    %s" % str(exc).splitlines()[0])
                print("  revising again — which is exactly what the loop's next "
                      "round does")
                continue
            source_2 = candidate
            break
        if not check("a revision built and checked", report_2 is not None):
            return 1
        second = verdict(report_2)
        print("\n  round 2 verdict: %s" % second)

        check("the revision was written over the same file",
              script.read_text(encoding="utf-8") == source_2, str(script))
        check("spec.json was not clobbered by the revision", spec.is_file())
        check("round 2 is not a fail", second in ("PASS", "WARN"), second)
        if first == "FAIL":
            check("the loop moved the verdict off FAIL", second != "FAIL",
                  "%s -> %s" % (first, second))
        scene = server.get_scene_info()
        check("the revised part replaced the object in place",
              scene.count(SLUG) == 1, scene)

        print("\n  loop: %s -> %s" % (first, second))
        return 1 if _FAILURES else 0
    except ForgeError as exc:
        _FAILURES.append("a tool raised: %s" % str(exc).splitlines()[0])
        print("\nFORGE ERROR: %s" % exc)
        return 1
    finally:
        stop_file.write_text("stop", encoding="utf-8")
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        # The project folder is a test artifact: projects/ stays real projects only.
        shutil.rmtree(REPO_ROOT / "projects" / SLUG, ignore_errors=True)
        print("\ncleaned up projects/%s (exists: %s)"
              % (SLUG, (REPO_ROOT / "projects" / SLUG).exists()))
        print("blender log: %s" % (tmpdir / "blender.log"))
        if _FAILURES:
            print("FAILED: %s" % ", ".join(_FAILURES))
        print("RESULT: %s" % ("FAILURES" if _FAILURES else "OK"))


if __name__ == "__main__":
    sys.exit(main())
