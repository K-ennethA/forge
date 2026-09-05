"""Headless add-on tests for Phase 6c (reference images).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_reference.py

The socket port is **9886** (9876 belongs to a live session, 9877-9885 to the
earlier phases and their harnesses).  Nothing else is needed: no geometry
service, no Claude CLI, no network beyond loopback — the test images are written
byte by byte in this file with ``zlib``/``struct``, so the suite carries its own
fixtures and leaves nothing behind.

What is actually being proved:

1. ``load_reference`` is a protocol command and makes an EMPTY of type IMAGE —
   never geometry, so a reference can never end up in an export;
2. each view is rotated so the picture faces whoever is looking from that
   orthographic view, and sits a little way behind the origin so it does not
   z-fight with the model;
3. the size maths: ``size_mm`` is the picture's longer side, the shorter side
   follows the file's pixel aspect, and both come back in millimetres;
4. it is half transparent (an opaque reference hides the thing you are
   comparing it against);
5. loading the same name twice REPLACES rather than leaving ``Ref-front.001``;
6. a missing file, a folder, a .txt and a file that is not really an image each
   fail with a sentence rather than a traceback;
7. the panel side: the attach field registers, validates, is drawn as a chip
   with a clear button, and clears itself after a successful send.
"""

import json
import math
import os
import socket as socketlib
import struct
import sys
import tempfile
import threading
import time
import traceback
import zlib

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9886  # not 9876..9885 (live session + phases 2-7 and their harnesses)
BRIDGE_PORT = 9887  # the fake assistant bridge for the panel section
BRIDGE_URL = "http://127.0.0.1:%d" % BRIDGE_PORT

#: Deliberately not square, and wider than it is tall, so "the longer side is
#: size_mm" is a claim with a right and a wrong answer.
IMAGE_W = 40
IMAGE_H = 25

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
# fixtures: a real PNG, written here so the suite depends on no asset
# ---------------------------------------------------------------------------

def write_png(path, width, height):
    """A minimal, valid, opaque-grey PNG. Enough for Blender to report a size."""

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes([90, 110, 130] * width) for _ in range(height))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)
    return path


def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=60.0):
    """One socket command, pumping the main-thread queue until it replies."""
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


def load_reference(**params):
    return _roundtrip({"type": "load_reference", "params": params})


def drop(name):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


# ---------------------------------------------------------------------------
# tests — the socket command
# ---------------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import registry

    check("load_reference is a protocol command", registry.has_command("load_reference"))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "load_meshes", "partforge_open", "export_stl",
               "flow_list", "flow_run", "rigforge_tag")),
          str(len(registry.command_names())))


def test_front_reference(image):
    section("load_reference front")
    drop("Ref-front")
    reply = load_reference(path=image, view="front")
    if not check("the command succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply["result"]
    check("it is named Ref-front by default", result.get("object") == "Ref-front",
          str(result.get("object")))

    obj = bpy.data.objects.get("Ref-front")
    if not check("the object exists in the file", obj is not None):
        return
    check("it is an EMPTY, not geometry (so it can never be exported)",
          obj.type == "EMPTY", obj.type)
    check("displayed as an image", obj.empty_display_type == "IMAGE",
          obj.empty_display_type)
    check("with our picture attached",
          getattr(obj.data, "filepath", "") and
          os.path.normcase(bpy.path.abspath(obj.data.filepath))
          == os.path.normcase(image),
          str(getattr(obj.data, "filepath", None)))

    # front view (Numpad 1) looks along +Y, so the picture faces -Y: +90 about X
    degrees = [round(math.degrees(v), 2) for v in obj.rotation_euler]
    check("rotated to face the front view (90, 0, 0)",
          abs(degrees[0] - 90.0) < 0.01 and abs(degrees[1]) < 0.01
          and abs(degrees[2]) < 0.01, str(degrees))
    check("and it is sitting a little way behind the origin, on +Y",
          obj.location.y > 0 and abs(obj.location.x) < 1e-9
          and abs(obj.location.z) < 1e-9,
          str(tuple(round(v, 5) for v in obj.location)))
    check("by a millimetre, in metres", abs(obj.location.y - 0.001) < 1e-7,
          str(obj.location.y))

    # size: the LONGER side is size_mm, the shorter follows the pixel aspect
    check("the display size is 200 mm in scene metres by default",
          abs(obj.empty_display_size - 0.200) < 1e-6, str(obj.empty_display_size))
    check("the reported width is the longer side (200 mm)",
          abs(result.get("width_mm", 0) - 200.0) < 0.01, str(result.get("width_mm")))
    check("the height follows the picture's aspect, undistorted",
          abs(result.get("height_mm", 0) - 200.0 * IMAGE_H / IMAGE_W) < 0.01,
          "%s (expected %.1f)" % (result.get("height_mm"), 200.0 * IMAGE_H / IMAGE_W))
    check("and it says what the file's pixels were",
          result.get("pixels") == [IMAGE_W, IMAGE_H], str(result.get("pixels")))

    check("it is half transparent so the model shows through",
          obj.use_empty_image_alpha and abs(obj.color[3] - 0.5) < 1e-6,
          "%s / %s" % (obj.use_empty_image_alpha, round(obj.color[3], 3)))
    check("and visible in both projections",
          obj.show_empty_image_orthographic and obj.show_empty_image_perspective,
          "%s / %s" % (obj.show_empty_image_orthographic,
                       obj.show_empty_image_perspective))
    check("the result carries the contract's three fields",
          {"object", "width_mm", "height_mm"} <= set(result), str(sorted(result)))


def test_side_and_top(image):
    section("the other two views")
    for name, view, expected, axis in (
        ("Ref-side", "side", (90.0, 0.0, 90.0), ("x", -0.001)),
        ("Ref-top", "top", (0.0, 0.0, 0.0), ("z", -0.001)),
    ):
        drop(name)
        reply = load_reference(path=image, view=view)
        if not check("%s loaded" % view, reply.get("status") == "success",
                     str(reply.get("message"))[:300]):
            continue
        obj = bpy.data.objects.get(name)
        if not check("it is called %s" % name, obj is not None):
            continue
        degrees = [round(math.degrees(v), 2) for v in obj.rotation_euler]
        check("%s faces its own orthographic view %s" % (view, expected),
              all(abs(degrees[i] - expected[i]) < 0.01 for i in range(3)),
              str(degrees))
        component, value = axis
        check("and steps back along %s%s" % ("-" if value < 0 else "+", component),
              abs(getattr(obj.location, component) - value) < 1e-7,
              str(tuple(round(v, 5) for v in obj.location)))
    note("front faces -Y, side faces +X, top faces +Z (up at the viewer); "
         "each offset pushes the plane away from that viewer")


def test_size_and_name(image):
    section("size_mm and name")
    drop("BowlSketch")
    reply = load_reference(path=image, view="front", size_mm=150.0, name="BowlSketch")
    if not check("a named reference at 150 mm loaded",
                 reply.get("status") == "success", str(reply.get("message"))[:300]):
        return
    result = reply["result"]
    obj = bpy.data.objects.get("BowlSketch")
    check("it took the name it was given", obj is not None, str(result.get("object")))
    if obj is not None:
        check("and the size it was given", abs(obj.empty_display_size - 0.150) < 1e-6,
              str(obj.empty_display_size))
    check("the longer side is 150 mm", abs(result.get("width_mm", 0) - 150.0) < 0.01,
          str(result.get("width_mm")))
    check("the shorter side scaled with it",
          abs(result.get("height_mm", 0) - 150.0 * IMAGE_H / IMAGE_W) < 0.01,
          str(result.get("height_mm")))


def test_reload_replaces(image, tall_image):
    section("loading the same name twice replaces, never duplicates")
    before = len([o for o in bpy.data.objects if o.name.startswith("Ref-front")])
    reply = load_reference(path=tall_image, view="front")
    if not check("the second load succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    names = sorted(o.name for o in bpy.data.objects if o.name.startswith("Ref-front"))
    check("there is still exactly one Ref-front", names == ["Ref-front"], str(names))
    check("and no .001 was created", before == len(names), str(names))

    obj = bpy.data.objects.get("Ref-front")
    check("it now shows the new picture",
          obj is not None and os.path.normcase(bpy.path.abspath(obj.data.filepath))
          == os.path.normcase(tall_image),
          str(getattr(getattr(obj, "data", None), "filepath", None)))
    result = reply["result"]
    check("the result says it replaced one", result.get("replaced") is True,
          str(result.get("replaced")))
    check("and the aspect flipped with the new file (taller than wide)",
          result.get("height_mm", 0) > result.get("width_mm", 0),
          "%s x %s" % (result.get("width_mm"), result.get("height_mm")))

    # put the original back so the rest of the suite sees what it expects
    load_reference(path=image, view="front")


def test_bad_input(image, tmpdir):
    section("the failures an artist will actually hit")
    missing = os.path.join(tmpdir, "not-here.png")
    reply = load_reference(path=missing)
    check("a path that is not there says so",
          reply.get("status") == "error" and "no file at" in
          str(reply.get("message")).lower(), str(reply.get("message"))[:200])

    text_file = os.path.join(tmpdir, "notes.txt")
    with open(text_file, "w", encoding="utf-8") as handle:
        handle.write("not a picture")
    reply = load_reference(path=text_file)
    check("a file that is not a picture says which types work",
          reply.get("status") == "error" and ".png" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    broken = os.path.join(tmpdir, "broken.png")
    with open(broken, "wb") as handle:
        handle.write(b"this is not a PNG at all")
    reply = load_reference(path=broken)
    check("a .png Blender cannot read fails cleanly, not with a traceback",
          reply.get("status") == "error" and "image" in str(reply.get("message")).lower(),
          str(reply.get("message"))[:200])
    check("and no half-made empty was left behind",
          bpy.data.objects.get("Ref-front") is not None
          and not any(o.name.startswith("Ref-front.") for o in bpy.data.objects),
          str([o.name for o in bpy.data.objects if o.name.startswith("Ref")]))

    reply = load_reference(path=tmpdir)
    check("a folder is refused",
          reply.get("status") == "error" and "folder" in str(reply.get("message")).lower(),
          str(reply.get("message"))[:200])

    reply = load_reference(path="")
    check("and an empty path is refused",
          reply.get("status") == "error", str(reply.get("message"))[:200])

    reply = load_reference(path=image, view="diagonal")
    check("an unknown view names the three that exist",
          reply.get("status") == "error"
          and all(word in str(reply.get("message")).lower()
                  for word in ("front", "side", "top")),
          str(reply.get("message"))[:200])

    bpy.ops.mesh.primitive_cube_add(size=0.05)
    cube = bpy.context.view_layer.objects.active
    cube.name = "NotAnEmpty"
    reply = load_reference(path=image, name="NotAnEmpty")
    check("and a name already taken by a mesh is refused rather than clobbered",
          reply.get("status") == "error"
          and "NotAnEmpty" in str(reply.get("message")),
          str(reply.get("message"))[:200])
    check("the mesh is untouched",
          bpy.data.objects.get("NotAnEmpty") is not None
          and bpy.data.objects["NotAnEmpty"].type == "MESH")


def test_it_is_not_exportable(image, tmpdir):
    section("a reference can never end up in a print")
    from forge.tools import registry

    status, _result, message = registry.dispatch(
        "export_stl", {"objects": ["Ref-front"],
                       "path": os.path.join(tmpdir, "ref.stl")})
    check("export_stl refuses the image empty by name",
          status == "error" and "Ref-front" in message, message[:200])
    check("and wrote nothing", not os.path.exists(os.path.join(tmpdir, "ref.stl")))


# ---------------------------------------------------------------------------
# tests — the panel side
# ---------------------------------------------------------------------------

def test_panel_props():
    section("the attach field")
    from forge.tools import assistant

    props = assistant.get_props(bpy.context)
    if not check("scene.forge_assistant exists", props is not None):
        return None
    check("the chat props carry image_path", hasattr(props, "image_path"))
    check("it opens Blender's file browser (subtype FILE_PATH)",
          props.bl_rna.properties["image_path"].subtype == "FILE_PATH",
          str(props.bl_rna.properties["image_path"].subtype))
    check("forge.assistant_clear_image is registered",
          hasattr(bpy.ops.forge, "assistant_clear_image"))
    check("and the older assistant operators are all still there",
          all(hasattr(bpy.ops.forge, name) for name in
              ("assistant_send", "assistant_new", "assistant_cancel",
               "assistant_health")))
    return props


def test_panel_validation(props, image, tmpdir):
    section("what the panel refuses before sending")
    from forge.tools import assistant

    check("no attachment is not a problem", assistant.image_problem("") == "",
          assistant.image_problem(""))
    check("a real picture passes", assistant.image_problem(image) == "",
          assistant.image_problem(image))
    for extension in (".png", ".PNG", ".jpg", ".jpeg", ".webp", ".bmp"):
        candidate = os.path.join(tmpdir, "ref" + extension)
        write_png(candidate, 4, 4)
        check("%s is accepted" % extension, assistant.image_problem(candidate) == "",
              assistant.image_problem(candidate))

    missing = os.path.join(tmpdir, "gone.png")
    check("a missing file says where it looked",
          "no file at" in assistant.image_problem(missing).lower(),
          assistant.image_problem(missing))
    text_file = os.path.join(tmpdir, "notes.txt")
    problem = assistant.image_problem(text_file)
    check("a .txt says what IS a picture",
          "not a picture" in problem and ".png" in problem, problem)
    check("a folder is called a folder", "folder" in assistant.image_problem(tmpdir),
          assistant.image_problem(tmpdir))

    check("the chip shows the filename, not the path",
          assistant.image_label(image) == os.path.basename(image),
          assistant.image_label(image))
    check("and nothing when there is no attachment",
          assistant.image_label("") == "", assistant.image_label(""))

    check("the panel's accepted types match the bridge's",
          assistant.IMAGE_EXTENSIONS == (".png", ".jpg", ".jpeg", ".webp", ".bmp"),
          str(assistant.IMAGE_EXTENSIONS))

    from forge.tools import common

    check("and the socket command accepts exactly the same list",
          set(common.REFERENCE_EXTENSIONS) == set(assistant.IMAGE_EXTENSIONS),
          str(common.REFERENCE_EXTENSIONS))

    # a bad attachment must stop the send, with the reason in the status line
    props.image_path = missing
    props.message = "what is this?"
    props.log.clear()
    result = bpy.ops.forge.assistant_send()
    check("Send is cancelled when the picture is not there",
          "CANCELLED" in result, str(result))
    check("the reason is in the status line",
          props.status_is_error and "no file at" in props.status.lower(), props.status)
    check("the message is still in the box, not lost",
          props.message == "what is this?", props.message)
    check("and nothing was written to the chat log", len(props.log) == 0,
          str(len(props.log)))
    props.image_path = ""
    props.message = ""


def test_panel_send_carries_the_path(props, fake, image):
    section("Send with a picture attached")
    from forge.tools import assistant

    assistant.set_status(props, "")
    props.log.clear()
    props.image_path = image
    props.message = "how big should this be?"

    result = bpy.ops.forge.assistant_send()
    check("Send finished", "FINISHED" in result, str(result))

    asks = fake.asks()
    if check("the bridge received a message", len(asks) >= 1, str(len(asks))):
        context = asks[-1].get("context") or {}
        check("with the image path in the context",
              os.path.normcase(str(context.get("image_path") or ""))
              == os.path.normcase(image), str(context.get("image_path")))
        check("as an absolute path",
              os.path.isabs(str(context.get("image_path") or "")),
              str(context.get("image_path")))
        check("and the scene context is still there too",
              "blender_version" in context, str(sorted(context)))
    check("the attachment cleared after the answer landed", props.image_path == "",
          props.image_path)
    check("the chat log holds the exchange", len(props.log) == 2, str(len(props.log)))

    # the next message must NOT quietly re-send the same picture
    props.message = "and how thick?"
    bpy.ops.forge.assistant_send()
    context = (fake.asks()[-1].get("context") or {})
    check("the next message carries no attachment",
          "image_path" not in context, str(sorted(context)))


def test_clear_button(props, image):
    section("the X on the chip")
    props.image_path = image
    result = bpy.ops.forge.assistant_clear_image()
    check("the operator finished", "FINISHED" in result, str(result))
    check("and the attachment is gone", props.image_path == "", props.image_path)
    check("with a status line saying so", "removed" in props.status.lower(),
          props.status)


def test_panel_draw(props, image):
    section("the Assistant box draws the chip")
    import re

    from forge.ui import panels

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_assistant")[1].split("\nclass ")[0]

    used = sorted(set(re.findall(r'\.prop\(chat,\s*"([a-z_]+)"', body)))
    check("the panel draws the attach field", "image_path" in used, str(used))
    unknown = [name for name in used if not hasattr(props, name)]
    check("every property it draws exists", not unknown, str(unknown))
    check("it still binds only to `chat`",
          not re.search(r'\.prop\((props|rf|ra|fl),\s*"', body),
          "the Assistant panel must bind its state to `chat`")

    operators = sorted(set(re.findall(r'\.operator\(\s*"(forge\.assistant_[a-z_]+)"',
                                      body)))
    check("the clear button is on the panel",
          "forge.assistant_clear_image" in operators, str(operators))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every button maps to a registered operator", not missing, str(missing))
    check("the chip is drawn from the filename helper",
          "image_label" in body, "the chip must show the basename")

    check("the older boxes are all still registered",
          all(getattr(bpy.types, name, None) is not None
              for name in ("VIEW3D_PT_forge_assistant", "VIEW3D_PT_forge_partforge",
                           "VIEW3D_PT_forge_flows", "VIEW3D_PT_forge_rigforge",
                           "VIEW3D_PT_forge_server")))


# ---------------------------------------------------------------------------
# the fake bridge (the panel section only; the real one is tested in assistant/)
# ---------------------------------------------------------------------------

class FakeBridge(object):
    def __init__(self, port):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.calls = []
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

            def do_GET(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                if path.startswith("/job/"):
                    self._send(200, outer.jobs.get(path[len("/job/"):],
                                                   {"state": "error",
                                                    "error": "no such job"}))
                    return
                self._send(200, {"status": "ok", "busy": False,
                                 "claude_cli": {"found": True, "version": "9.9.9"}})

            def do_POST(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                length = int(self.headers.get("Content-Length") or 0)
                payload = {}
                if length:
                    try:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        payload = {}
                outer.calls.append(("POST", path, payload))
                if path == "/ask":
                    job_id = "job-%d" % (len(outer.jobs) + 1)
                    outer.jobs[job_id] = {"state": "done", "job_id": job_id,
                                          "reply": "Looked at it.",
                                          "session_id": "sess-ref-1"}
                    self._send(200, {"job_id": job_id, "state": "running"})
                    return
                self._send(200, {"status": "ok"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def asks(self):
        return [payload for _method, path, payload in self.calls if path == "/ask"]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def point_at(url):
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["assistant_url"] = url
    forge_prefs._FALLBACK.assistant_url = url
    entry = bpy.context.preferences.addons.get(forge_prefs.ADDON_ID)
    if entry is not None and entry.preferences is not None:
        try:
            entry.preferences.assistant_url = url
        except (AttributeError, TypeError):
            pass


def test_ports_are_free_after():
    section("the ports let go")
    from forge import server as forge_server

    forge_server.stop_server()
    for port in (PORT, BRIDGE_PORT):
        probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
            freed = True
        except OSError as exc:
            freed = False
            note(str(exc))
        finally:
            probe.close()
        check("port %d is free again" % port, freed)


# ---------------------------------------------------------------------------

def main():
    print("Forge add-on Phase 6c (reference images) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_ref_")
    image = write_png(os.path.join(tmpdir, "sketch.png"), IMAGE_W, IMAGE_H)
    tall = write_png(os.path.join(tmpdir, "tall.png"), IMAGE_H, IMAGE_W)
    note("test images in %s (%dx%d and %dx%d)"
         % (tmpdir, IMAGE_W, IMAGE_H, IMAGE_H, IMAGE_W))

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    fake = FakeBridge(BRIDGE_PORT)
    point_at(BRIDGE_URL)
    note("fake bridge on %s" % BRIDGE_URL)

    try:
        test_registration()
        test_front_reference(image)
        test_side_and_top(image)
        test_size_and_name(image)
        test_reload_replaces(image, tall)
        test_bad_input(image, tmpdir)
        test_it_is_not_exportable(image, tmpdir)
        props = test_panel_props()
        if props is None:
            raise AssertionError("no assistant props; the panel section needs them")
        test_panel_validation(props, image, tmpdir)
        test_panel_send_carries_the_path(props, fake, image)
        test_clear_button(props, image)
        test_panel_draw(props, image)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        fake.stop()
        try:
            test_ports_are_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
