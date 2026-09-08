"""Headless add-on tests for Phase 15: project .blend files and open-from-Library.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_projects.py

The socket port is **9899** (9876 is a live session, 9879-9894 the other
suites).  ``FORGE_PROJECTS_DIR`` is pointed at a temp folder before anything
runs, so a suite that writes ``.blend`` files can never leave one in the repo's
own ``projects/``.  Nothing outside loopback is touched and no service is
needed.

What is being proved, in order:

1. **``save_project_blend`` saves a copy, and a copy is the whole feature.**
   The file lands at ``projects/<name>/<name>.blend``, the project folder is
   created when it is missing, and ``bpy.data.filepath`` — where the artist's
   own Ctrl+S goes — is the same string before and after.  A tool that quietly
   moves where your work saves to is a tool nobody should hand a sculpt.
2. **The refusals name the fix**: a project name with a slash in it, a missing
   folder with ``create: false``, an open with nothing to open.
3. **``open_project_blend`` asks before it destroys.**  With unsaved work and no
   ``confirm``, it returns ``needs_confirmation`` plus what would be lost and
   touches nothing.  Confirmed, it replaces the world.
4. **THE question this feature stood or fell on: does the socket server survive
   ``wm.open_mainfile``?**  A file load clears Blender's timer list, and the
   add-on's main-thread pump *is* a timer — if it went with the file, every
   Forge command after an open would hang forever on a queue nothing drains.
   So this suite registers a second, deliberately **non-persistent** timer
   beside the pump and asserts, after the open, that the control timer is *gone*
   and the pump is still there.  Without the control the assertion would be
   worthless: "the pump survived" could just mean "nothing was ever cleared".
   Then it round-trips a real command through the real socket to prove the
   answer in practice as well as in the registry.
5. **Neither command pushes an undo checkpoint** — one writes a file (the
   ``export_stl`` precedent), and the other destroys the very stack it would be
   pushing onto.
6. The panel's Save button is wired to the same command the socket is.
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

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9899  # not 9876 (live) and not 9879..9894 (the other suites)

#: Every project this suite writes lives here, never in the repo.
SANDBOX = os.path.join(tempfile.gettempdir(), "forge-headless-projects")

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
# the socket round trip (same shape as every other suite)
# ---------------------------------------------------------------------------

def _roundtrip(payload, timeout=120.0):
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
    return box.get("reply") or {"status": "error", "message": "no reply"}


def send(command, **params):
    return _roundtrip({"type": command, "params": params})


def result_of(reply):
    return reply.get("result") or {}


def message_of(reply):
    return str(reply.get("message") or "")


# ---------------------------------------------------------------------------
# scene helpers
# ---------------------------------------------------------------------------

def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)


def add_cube(name):
    bpy.ops.mesh.primitive_cube_add(size=0.05)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def fresh_sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    os.environ["FORGE_PROJECTS_DIR"] = SANDBOX


# ---------------------------------------------------------------------------
# a fake UILayout, so the PartForge box's draw() runs headless
# ---------------------------------------------------------------------------

class FakeLayout(object):
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
        getattr(data, name)
        self._sink["props"].append(name)
        return None

    def operator(self, idname, **kwargs):
        self._sink["operators"].append(str(idname))
        return type("Args", (object,), {})()

    @property
    def sink(self):
        return self._sink


# ---------------------------------------------------------------------------
# 1. where projects live
# ---------------------------------------------------------------------------

def test_projects_dir():
    section("where projects live")
    from forge.tools import projects

    check("FORGE_PROJECTS_DIR wins over the repo folder",
          os.path.normcase(projects.projects_dir()) == os.path.normcase(SANDBOX),
          projects.projects_dir())
    check("blend_path is projects/<name>/<name>.blend",
          projects.blend_path("cup") == os.path.join(SANDBOX, "cup", "cup.blend"),
          projects.blend_path("cup"))

    for bad in ("../escape", "a/b", "a\\b", "", "   ", ".", "..", "a%2fb"):
        try:
            projects.check_name(bad)
            ok = False
        except Exception as exc:  # noqa: BLE001
            ok = "project" in str(exc).lower() or "name" in str(exc).lower()
        check("a name like %r is refused by its shape" % bad, ok)


# ---------------------------------------------------------------------------
# 2. save_project_blend
# ---------------------------------------------------------------------------

def test_save_writes_a_copy():
    section("save_project_blend — a copy, never a retarget")
    clear_scene()
    add_cube("cup")
    add_cube("cup-collar")

    before = str(bpy.data.filepath or "")
    reply = send("save_project_blend", project="cup")
    check("save_project_blend succeeded", reply.get("status") == "success",
          message_of(reply))
    result = result_of(reply)

    path = os.path.join(SANDBOX, "cup", "cup.blend")
    check("the .blend is at projects/cup/cup.blend",
          os.path.isfile(path), result.get("path"))
    check("the answer names the same path", result.get("path") == path,
          result.get("path"))
    check("the project folder was created", result.get("created_folder") is True)
    check("nothing was replaced the first time",
          result.get("replaced") is False)
    check("the size is real", int(result.get("size") or 0) > 0,
          result.get("size"))
    check("it counted the objects it saved",
          int(result.get("object_count") or 0) == len(bpy.data.objects),
          result.get("object_count"))

    # THE assertion. copy=True or the artist's own file moves under them.
    after = str(bpy.data.filepath or "")
    check("bpy.data.filepath is untouched by the save (copy=True)",
          after == before, "%r -> %r" % (before, after))
    check("the answer hands the caller that proof",
          result.get("session_file") == after and result.get("retargeted") is False,
          result.get("session_file"))
    check("the 'where' line says the artist's own file is untouched",
          "untouched" in str(result.get("where") or "").lower(),
          result.get("where"))


def test_save_again_replaces():
    section("save_project_blend — saving over yesterday's save")
    add_cube("cup-handle")
    reply = send("save_project_blend", project="cup")
    result = result_of(reply)
    check("the second save says it replaced the first",
          result.get("replaced") is True and result.get("created_folder") is False,
          result)
    check("and it still did not retarget the session",
          result.get("session_file") == str(bpy.data.filepath or ""))


def test_save_defaults_to_the_panels_project():
    section("save_project_blend — the project the panel is showing")
    props = bpy.context.scene.forge_partforge
    kept = props.script_path
    props.script_path = os.path.join(SANDBOX, "lamp", "part.py")
    try:
        reply = send("save_project_blend")
        result = result_of(reply)
        check("with no arguments it saves into the panel's own project",
              result.get("project") == "lamp", result)
        check("and the file is there",
              os.path.isfile(os.path.join(SANDBOX, "lamp", "lamp.blend")))
    finally:
        props.script_path = kept

    reply = send("save_project_blend")
    check("with no panel script and no argument it says which parameter is "
          "missing",
          reply.get("status") == "error" and "project" in message_of(reply),
          message_of(reply))


def test_save_refusals():
    section("save_project_blend — the refusals")
    reply = send("save_project_blend", project="../escape")
    check("a name with a slash in it is refused",
          reply.get("status") == "error" and "project" in message_of(reply).lower(),
          message_of(reply))
    check("and nothing was written outside the sandbox",
          not os.path.exists(os.path.join(os.path.dirname(SANDBOX), "escape.blend")))

    reply = send("save_project_blend", project="never-made", create=False)
    check("create: false on a folder that is not there says so",
          reply.get("status") == "error"
          and "create" in message_of(reply).lower(),
          message_of(reply))
    check("and it did not make the folder anyway",
          not os.path.isdir(os.path.join(SANDBOX, "never-made")))

    reply = send("save_project_blend", project=17)
    check("a project that is not a string is refused",
          reply.get("status") == "error", message_of(reply))


# ---------------------------------------------------------------------------
# 3. open_project_blend — the missing file and the confirmation
# ---------------------------------------------------------------------------

def test_open_missing_file():
    section("open_project_blend — nothing to open")
    reply = send("open_project_blend", name="never-saved")
    check("opening a project with no .blend is an error",
          reply.get("status") == "error", reply)
    check("and the error names the button that makes one",
          "save scene to project" in message_of(reply).lower(),
          message_of(reply))

    reply = send("open_project_blend")
    check("open with no name at all says which parameter is missing",
          reply.get("status") == "error" and "name" in message_of(reply).lower(),
          message_of(reply))

    reply = send("open_project_blend", name="../escape")
    check("a name with a slash in it is refused before any path arithmetic",
          reply.get("status") == "error", message_of(reply))


def test_open_asks_before_it_destroys():
    section("open_project_blend — the confirmation round trip")
    # Something unsaved in the scene, which is what the question is about.
    add_cube("work-in-progress")
    check("the session is dirty, which is the case worth asking about",
          bool(bpy.data.is_dirty))

    names_before = sorted(obj.name for obj in bpy.data.objects)
    reply = send("open_project_blend", name="cup")
    result = result_of(reply)
    check("an unconfirmed open is a success, not an error",
          reply.get("status") == "success", message_of(reply))
    check("it asks instead of opening",
          result.get("needs_confirmation") is True and result.get("opened") is False,
          result)
    check("it says what would be lost, with a count",
          "object" in str(result.get("would_lose") or "").lower(),
          result.get("would_lose"))
    check("and it names the way to keep the work",
          "save scene to project" in str(result.get("hint") or "").lower(),
          result.get("hint"))
    check("nothing in the scene moved",
          sorted(obj.name for obj in bpy.data.objects) == names_before)
    check("and the session file is still whatever it was",
          result.get("session_file") == str(bpy.data.filepath or ""))


# ---------------------------------------------------------------------------
# 4. THE question: does the server survive wm.open_mainfile?
# ---------------------------------------------------------------------------

def _control_timer():
    """A deliberately NON-persistent timer, registered only to be destroyed.

    It is the control in the experiment: if a file load clears it and leaves the
    pump alone, then persistence is what saved the pump.  If it survived too,
    the file load never cleared any timers and the pump proves nothing.
    """
    return 60.0


def test_the_server_survives_the_world_being_replaced():
    section("open_project_blend — the server survives the file load")
    from forge import server as forge_server

    if not bpy.app.timers.is_registered(_control_timer):
        bpy.app.timers.register(_control_timer, first_interval=60.0)

    check("the pump is a registered timer before the open",
          bpy.app.timers.is_registered(forge_server._pump))
    check("the non-persistent control timer is registered too",
          bpy.app.timers.is_registered(_control_timer))
    check("the pump was registered persistent=True",
          _pump_is_persistent(forge_server))

    server_before = forge_server._server
    handled_before = server_before.commands_handled
    connections_before = server_before.total_connections

    # Prove there is something to lose, so this is the interesting path.
    add_cube("about-to-be-discarded")
    reply = send("open_project_blend", name="cup", confirm=True)
    result = result_of(reply)

    check("the confirmed open answered at all — the reply came back THROUGH "
          "the socket the open just reloaded the world under",
          reply.get("status") == "success", message_of(reply))
    check("it opened the project file",
          result.get("opened") is True and result.get("needs_confirmation") is False,
          result)
    check("and it said what it discarded, in the same words it would have "
          "asked with",
          "object" in str(result.get("discarded") or "").lower(),
          result.get("discarded"))
    check("the session file is now the project .blend",
          os.path.normcase(str(bpy.data.filepath or ""))
          == os.path.normcase(os.path.join(SANDBOX, "cup", "cup.blend")),
          bpy.data.filepath)
    check("the scene that came back is the one that was saved",
          "cup" in [obj.name for obj in bpy.data.objects],
          [obj.name for obj in bpy.data.objects])
    check("and the work in progress is gone, as promised",
          "about-to-be-discarded" not in [obj.name for obj in bpy.data.objects])

    # -- the experiment ---------------------------------------------------
    control_gone = not bpy.app.timers.is_registered(_control_timer)
    pump_alive = bpy.app.timers.is_registered(forge_server._pump)
    check("the file load DID clear Blender's timers (the control is gone)",
          control_gone,
          "the control timer survived, so this proves nothing about the pump")
    check("the persistent pump timer survived the file load", pump_alive)
    check("the add-on itself agrees, in the answer it sent back",
          result.get("pump_survived") is True, result.get("pump_survived"))

    check("the server object is the same one, not a replacement",
          forge_server._server is server_before)
    check("it is still running", forge_server.is_running() is True)
    check("on the same port",
          int(result.get("server_port") or 0) == PORT, result.get("server_port"))
    check("the pump callback still finds a live server after the open",
          forge_server._pump() == forge_server.PUMP_INTERVAL)

    # …and the real proof: a second command, through the same socket, after
    # the world was replaced under it.
    after = send("get_scene_info")
    check("a command still round-trips through the socket after the open",
          after.get("status") == "success", message_of(after))
    check("and it describes the file that was opened",
          "cup" in [item.get("name")
                    for item in (result_of(after).get("objects") or [])],
          result_of(after).get("objects"))
    check("the same server counted both commands",
          forge_server._server.commands_handled > handled_before
          and forge_server._server.total_connections > connections_before)

    ping = send("ping")
    check("ping answers too", ping.get("status") == "success")

    try:
        bpy.app.timers.unregister(_control_timer)
    except (ValueError, TypeError):
        pass


def _pump_is_persistent(forge_server):
    """Is ``_ensure_pump`` registering the pump with ``persistent=True``?

    Read off the source rather than guessed: ``bpy.app.timers`` exposes no way
    to ask a registered callback whether it is persistent, and the flag is the
    entire reason the add-on survives an open.
    """
    import inspect

    try:
        source = inspect.getsource(forge_server._ensure_pump)
    except (OSError, TypeError):
        return False
    return "persistent=True" in source.replace(" ", "")


def test_a_clean_scene_opens_without_being_asked():
    section("open_project_blend — nothing to lose, nothing to ask")
    # An empty scene has nothing to lose whatever Blender's dirty flag says, and
    # asking "are you sure?" about nothing is how a confirmation dialog trains
    # someone to click through the one that mattered.  It is also the only way
    # to reach this branch headless: `--background` leaves `is_dirty` true from
    # the first line of the session and an explicit save does not clear it
    # (measured on 5.0.1), which is exactly why the gate is not that flag alone.
    clear_scene()
    check("the scene is empty, so there is nothing to lose",
          not list(bpy.data.objects), [o.name for o in bpy.data.objects])
    check("…even though --background still calls the session dirty",
          bool(bpy.data.is_dirty))
    reply = send("open_project_blend", name="cup")
    result = result_of(reply)
    check("so it opens without asking",
          result.get("opened") is True and result.get("needs_confirmation") is False,
          result)
    check("and it says nothing was discarded",
          not str(result.get("discarded") or ""))


def test_saving_the_file_you_have_open():
    section("save_project_blend — saving over the file you are in")
    before = str(bpy.data.filepath or "")
    add_cube("added-after-open")
    reply = send("save_project_blend", project="cup")
    result = result_of(reply)
    check("saving into the file you have open works",
          reply.get("status") == "success", message_of(reply))
    check("and STILL does not blank or move the session path",
          str(bpy.data.filepath or "") == before,
          "%r -> %r" % (before, bpy.data.filepath))
    check("the answer says so", result.get("session_file") == before)


# ---------------------------------------------------------------------------
# 5. undo classification
# ---------------------------------------------------------------------------

def test_neither_command_pushes_an_undo_step():
    section("undo — a checkpoint neither command could keep")
    from forge.tools import registry

    check("save_project_blend is read-only for undo",
          "save_project_blend" in registry.READ_ONLY_COMMANDS)
    check("open_project_blend is read-only for undo",
          "open_project_blend" in registry.READ_ONLY_COMMANDS)
    check("push_undo declines both",
          registry.push_undo("save_project_blend") is False
          and registry.push_undo("open_project_blend") is False)


def test_both_commands_are_registered():
    section("the protocol")
    from forge.tools import registry

    names = registry.command_names()
    check("save_project_blend is a protocol command",
          "save_project_blend" in names)
    check("open_project_blend is a protocol command",
          "open_project_blend" in names)

    reply = send("save_project_blend", project="cup", nonsense=True)
    check("an unknown parameter is ignored rather than fatal",
          reply.get("status") == "success", message_of(reply))


# ---------------------------------------------------------------------------
# 6. the panel button
# ---------------------------------------------------------------------------

def test_panel_button():
    section("the PartForge box's Save button")
    from forge.ui.panels import VIEW3D_PT_forge_partforge

    layout = FakeLayout()
    shim = type("PanelShim", (object,), {})()
    shim.layout = layout
    try:
        VIEW3D_PT_forge_partforge.draw(shim, bpy.context)
    except Exception as exc:  # noqa: BLE001
        check("the PartForge box draws headless", False, str(exc))
        return
    check("the PartForge box carries Save Scene to Project",
          "forge.save_project_blend" in layout.sink["operators"],
          layout.sink["operators"])

    check("and the operator exists",
          hasattr(bpy.ops.forge, "save_project_blend"))

    props = bpy.context.scene.forge_partforge
    props.script_path = os.path.join(SANDBOX, "panelled", "part.py")
    try:
        result = bpy.ops.forge.save_project_blend()
    finally:
        props.script_path = ""
    check("pressing it saves into the panel's project",
          "FINISHED" in result
          and os.path.isfile(os.path.join(SANDBOX, "panelled", "panelled.blend")),
          result)
    check("and it reports what it did in the panel's own status line",
          "panelled.blend" in str(props.status), props.status)


def test_port_is_free_after():
    section("the port")
    probe = socketlib.socket()
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


def main():
    print("Forge add-on tests: Phase 15 — project .blend files and open-from-Library")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    fresh_sandbox()
    print("  projects sandbox: %s" % SANDBOX)

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    time.sleep(0.2)

    try:
        test_projects_dir()
        test_save_writes_a_copy()
        test_save_again_replaces()
        test_save_defaults_to_the_panels_project()
        test_save_refusals()
        test_open_missing_file()
        test_open_asks_before_it_destroys()
        # From here the world gets replaced, so nothing above may depend on
        # anything below.
        test_the_server_survives_the_world_being_replaced()
        test_a_clean_scene_opens_without_being_asked()
        test_saving_the_file_you_have_open()
        test_neither_command_pushes_an_undo_step()
        test_both_commands_are_registered()
        test_panel_button()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        forge_server.stop_server()
        time.sleep(0.2)
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        shutil.rmtree(SANDBOX, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
