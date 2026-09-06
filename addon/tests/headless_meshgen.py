"""Headless add-on tests for Phase 7 — a picture becomes a mesh.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_meshgen.py

The socket port is **9890** — 9876 belongs to the artist's live session and
9879 through 9889 to the other suites.  Nothing here touches the real meshgen
on 8902, the real geometry service on 8765 or a GPU: both are tiny stdlib HTTP
servers on ephemeral ports, and the ``.glb`` under test is one this script
exports from a cube seconds earlier.  A real generation is a manual gate that
costs five minutes of graphics card, not a test.

What is proved, item by item:

1. **import_generated** — a real glTF round trip (export a cube, import it
   back), the mandatory voxel repair, the adaptive voxel size, and the two
   refusals (wrong format, missing file);
2. **the Generate 3D from Picture button** — the whole job driven against a
   fake meshgen: submit, follow the stages, import the file it names;
3. **which picture it uses** — this box's field first, the Assistant box's
   attachment otherwise;
4. **the health dot** — five rows, meshgen up / models-missing / not running,
   and the rule that only meshgen being down is not an error;
5. **the panels still draw**, full and empty.
"""

import json
import os
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

PORT = 9890  # not 9876 (live session) and not 9879..9889 (every other suite)

_RESULTS = []
_TEMP = []


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
# fake meshgen (8902's shape) and a fake geometry service (for the print check)
# ---------------------------------------------------------------------------

#: What the job walks through. The stage changing mid-run is deliberate: the
#: panel has to show the stage NAME, because the bar resets every stage.
JOB_SCRIPT = [
    {"state": "queued", "progress": None, "stage": None},
    {"state": "running", "progress": 0.4, "stage": "Trellis2UpsampleStage"},
    {"state": "running", "progress": 0.2, "stage": "RemeshMesh"},
    {"state": "done", "progress": 1.0, "stage": None,
     "stats": {"verts": 8, "faces": 12}, "duration_ms": 304000,
     "model": "TRELLIS.2 int8 convrot",
     "vram": {"peak_gb": 8.15, "total_gb": 11.94}},
]

MESHGEN_STATE = {
    "polls": 0,
    "mesh_path": "",
    "health": "ok",          # or "models_missing"
    "job_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "posts": [],
}

HEALTH_OK = {
    "status": "ok",
    "service": "meshgen",
    "comfyui_running": False,
    "backend": {"name": "trellis2", "model": "TRELLIS.2 int8 convrot",
                "license": "MIT", "loaded": False, "vram_gb": 9, "ready": True},
    "available_backends": [{"name": "trellis2"}, {"name": "pixal3d"}],
    "jobs": {"active": 0, "queued": 0},
}

HEALTH_MISSING = dict(
    HEALTH_OK, status="models_missing",
    missing=[{"what": "trellis_2_shape_vae_bf16.safetensors",
              "path": r"C:\forge-models\models\vae\trellis_2_shape_vae_bf16.safetensors",
              "source": "https://huggingface.co/Comfy-Org/TRELLIS.2"}])


class _FakeMeshgen(BaseHTTPRequestHandler):
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

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/"):
            return self._send(200, HEALTH_OK if MESHGEN_STATE["health"] == "ok"
                              else HEALTH_MISSING)
        if path.startswith("/job/"):
            if path[len("/job/"):] != MESHGEN_STATE["job_id"]:
                return self._send(404, {"error": "no such job"})
            index = min(MESHGEN_STATE["polls"], len(JOB_SCRIPT) - 1)
            MESHGEN_STATE["polls"] += 1
            payload = dict(JOB_SCRIPT[index])
            payload.update({"job_id": MESHGEN_STATE["job_id"], "backend": "trellis2"})
            if payload.get("state") == "done":
                payload["mesh_path"] = MESHGEN_STATE["mesh_path"]
            return self._send(200, payload)
        return self._send(404, {"error": "no route"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0].rstrip("/")
        MESHGEN_STATE["posts"].append((path, body))
        if path == "/generate3d":
            MESHGEN_STATE["polls"] = 0
            return self._send(202, {"job_id": MESHGEN_STATE["job_id"],
                                    "state": "queued", "backend": "trellis2",
                                    "output": MESHGEN_STATE["mesh_path"]})
        return self._send(404, {"error": "no route"})


CHECK_MESH_REPLY = {
    "overall": "fail",
    "checks": [
        {"name": "bed_fit", "status": "pass", "details": "fits", "data": {}},
        {"name": "min_wall", "status": "fail", "details": "paper thin", "data": {}},
        {"name": "watertight", "status": "pass", "details": "closed",
         "data": {"solid_is_valid": None}},
    ],
    "stats": {"vertex_count": 8, "face_count": 12, "bounding_box_mm": [20, 20, 20]},
}


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
            return self._send(200, {"status": "ok", "build123d": "fake-1.0"})
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
        if path == "/check_mesh":
            return self._send(200, dict(CHECK_MESH_REPLY))
        self._send(404, {"error": "unknown endpoint %s" % path})


def start_fake(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


# ---------------------------------------------------------------------------
# harness (same shape as the other suites)
# ---------------------------------------------------------------------------

class FakeLayout(object):
    """Enough of ``UILayout`` to run a panel's draw() with nothing on screen."""

    def __init__(self, sink=None):
        self._sink = sink if sink is not None else {"labels": [], "operators": [],
                                                    "props": []}
        self.active = True
        self.alert = False
        self.enabled = True
        self.scale_y = 1.0
        self.scale_x = 1.0
        self.alignment = "EXPAND"
        self.use_property_split = False

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

    def label(self, text="", icon="NONE", **kwargs):
        self._sink["labels"].append(str(text))
        return None

    def prop(self, data, name, **kwargs):
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

    @property
    def sink(self):
        return self._sink

    def text(self):
        return "\n".join(self._sink["labels"])


class _FakeOperatorProps(object):
    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)


def draw_panel(panel_cls, context=None):
    layout = FakeLayout()
    shim = type("PanelShim", (object,), {})()
    shim.layout = layout
    panel_cls.draw(shim, context or bpy.context)
    return layout


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


def _roundtrip(payload, timeout=120.0):
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


def make_cube(name="GenSource", size_m=0.02):
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


def write_glb(path, size_m=0.02):
    """Export a cube as a .glb — a stand-in for what meshgen writes."""
    from forge.tools import common

    obj = make_cube("GenSource", size_m)
    with common.selection([obj], obj):
        status = bpy.ops.export_scene.gltf(
            **common.op_kwargs(bpy.ops.export_scene.gltf, {
                "filepath": path, "export_format": "GLB", "use_selection": True,
                "export_apply": True, "export_animations": False,
                "check_existing": False}))
    bpy.data.objects.remove(obj, do_unlink=True)
    return status


def write_png(path, size=8):
    """A tiny real PNG, so the picture checks are exercised on a real file."""
    image = bpy.data.images.new("gen_probe", size, size)
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


# ---------------------------------------------------------------------------
# 1. import_generated
# ---------------------------------------------------------------------------

def test_import_generated_round_trip(workdir):
    section("1. import_generated (export a cube, import it back)")
    from forge.tools import model, registry

    check("import_generated is a protocol command",
          registry.has_command("import_generated"))
    check("it is not read-only, so it gets an undo checkpoint",
          "import_generated" not in registry.READ_ONLY_COMMANDS)

    from forge.tools import flows

    check("and it is a legal flow step, so a saved job can end with it",
          flows._validate_step({"kind": "blender", "op": "import_generated"}, 0)
          is not None)

    glb = os.path.join(workdir, "generated.glb")
    status = write_glb(glb)
    if not check("a test .glb was exported", "FINISHED" in status and
                 os.path.isfile(glb), str(status)):
        return
    MESHGEN_STATE["mesh_path"] = glb

    for name in ("GeneratedCube", "RawCube"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    # --- the raw import first: no repair, so the mesh is what the file said
    reply = _roundtrip({"type": "import_generated", "params": {
        "path": glb, "name": "RawCube", "repair": False}})
    if not check("the raw import succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    raw = reply.get("result") or {}
    check("it is in the scene under the name that was asked for",
          "RawCube" in bpy.data.objects, str(raw.get("object")))
    check("and it says plainly that it was NOT repaired",
          raw.get("repaired") is False, str(raw.get("repaired")))
    check("a 20 mm cube comes back 20 mm, not 20 metres",
          all(abs(v - 20.0) < 0.5 for v in raw.get("dimensions_mm") or []),
          str(raw.get("dimensions_mm")))
    check("the glTF scene graph was collapsed to one object",
          bpy.data.objects["RawCube"].parent is None
          and "GenSource" not in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:8]))

    # --- and the repairing import, which is the default
    reply = _roundtrip({"type": "import_generated", "params": {
        "path": glb, "name": "GeneratedCube"}})
    if not check("the repairing import succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply.get("result") or {}
    check("repair is the DEFAULT, not something to ask for",
          result.get("repaired") is True, str(result.get("repaired")))
    check("the object is in the scene", "GeneratedCube" in bpy.data.objects,
          str(result.get("object")))
    check("with faces on it", (result.get("face_count") or 0) > 0,
          str(result.get("face_count")))
    check("the voxel size was worked out from the mesh, not hard-coded",
          0.0 < float(result.get("voxel_size") or 0) < 0.02,
          str(result.get("voxel_size")))
    check("and it is reported in millimetres too, for the artist",
          (result.get("voxel_size_mm") or 0) > 0, str(result.get("voxel_size_mm")))
    check("the counts before the repair are kept, so the report can say what changed",
          (result.get("before") or {}).get("face_count") == 12,
          str(result.get("before")))
    check("the repaired cube is still about 20 mm",
          all(abs(v - 20.0) < 4.0 for v in result.get("dimensions_mm") or []),
          str(result.get("dimensions_mm")))
    check("the Model box is now pointed at it",
          model.get_props(bpy.context).object_name == "GeneratedCube",
          model.get_props(bpy.context).object_name)

    # --- an explicit voxel size is honoured
    reply = _roundtrip({"type": "import_generated", "params": {
        "path": glb, "name": "CoarseCube", "voxel_size": 0.004,
        "collection": "Generated"}})
    result = reply.get("result") or {}
    check("an explicit voxel size wins over the adaptive one",
          abs(float(result.get("voxel_size") or 0) - 0.004) < 1e-9,
          str(result.get("voxel_size")))
    check("and the collection asked for is where it landed",
          "Generated" in bpy.data.collections
          and result.get("object") in bpy.data.collections["Generated"].objects,
          str(list(bpy.data.collections.keys())))


def test_import_generated_refusals(workdir):
    section("1b. the two refusals")
    stl = os.path.join(workdir, "not-a-glb.stl")
    with open(stl, "wb") as handle:
        handle.write(b"solid x\nendsolid x\n")

    reply = _roundtrip({"type": "import_generated", "params": {"path": stl}})
    message = str(reply.get("message") or "")
    check("an STL is refused rather than half-imported",
          reply.get("status") == "error", str(reply)[:200])
    check("and the message points at the button that DOES open it",
          "Import Model" in message, message[:200])

    reply = _roundtrip({"type": "import_generated", "params": {
        "path": os.path.join(workdir, "nothing-here.glb")}})
    check("a missing file says so plainly",
          reply.get("status") == "error"
          and "no file at" in str(reply.get("message") or "").lower(),
          str(reply.get("message"))[:200])


# ---------------------------------------------------------------------------
# 2 + 3. the Generate 3D from Picture button
# ---------------------------------------------------------------------------

def test_generate_button(workdir):
    section("2. Generate 3D from Picture (against a fake meshgen)")
    from forge.tools import assistant, model

    md = model.get_props(bpy.context)
    chat = assistant.get_props(bpy.context)
    check("the operator is registered", hasattr(bpy.ops.forge, "model_generate3d"))
    for name in ("image_path", "gen_stage", "gen_progress", "gen_job_id"):
        check("the Model props carry %s" % name, hasattr(md, name))

    picture = write_png(os.path.join(workdir, "gecko.png"))

    # --- which picture: this box's field wins, the chat box's is the fallback
    md.image_path = ""
    chat.image_path = picture
    path, source = model.picture_for(md, bpy.context)
    check("with nothing here, the Assistant box's attachment is used",
          path == picture and "Assistant" in source, source)
    own = write_png(os.path.join(workdir, "own.png"))
    md.image_path = own
    path, source = model.picture_for(md, bpy.context)
    check("and this box's own field wins when it is filled in",
          path == own and "Picture" in source, source)
    md.image_path = ""
    chat.image_path = ""
    problem = ""
    try:
        model.picture_for(md, bpy.context)
    except Exception as exc:  # noqa: BLE001
        problem = str(exc)
    check("with neither, it says to attach one rather than failing obscurely",
          "Attach a picture" in problem, problem[:120])

    # --- the whole job
    md.image_path = picture
    for name in ("generated_cube", "GeneratedCube"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    MESHGEN_STATE["posts"][:] = []
    MESHGEN_STATE["polls"] = 0

    result = bpy.ops.forge.model_generate3d()
    if not check("the button finished", "FINISHED" in result,
                 str(result) + " " + md.status):
        return
    posts = [entry for entry in MESHGEN_STATE["posts"] if entry[0] == "/generate3d"]
    if not check("it posted the picture to /generate3d", len(posts) == 1,
                 str([entry[0] for entry in MESHGEN_STATE["posts"]])):
        return
    check("with an absolute path to the file",
          os.path.isabs(posts[0][1].get("image_path") or "")
          and os.path.isfile(posts[0][1]["image_path"]),
          str(posts[0][1]))
    check("it followed the job to the end rather than sleeping through it",
          MESHGEN_STATE["polls"] >= len(JOB_SCRIPT), str(MESHGEN_STATE["polls"]))
    check("the mesh it named is in the scene, repaired",
          md.object_name in bpy.data.objects and md.face_count > 0,
          "%s / %d faces" % (md.object_name, md.face_count))
    check("and the status says how long it took, in minutes",
          "5 min" in md.status and not md.status_is_error, md.status)
    check("the live stage line is cleared once it is done", md.gen_stage == "",
          md.gen_stage)

    # the live line itself, without a five-minute wait to see it
    sentence = model.stage_sentence({"stage": "RemeshMesh", "state": "running",
                                     "seconds": 65})
    check("the live line names the stage and the time so far",
          "RemeshMesh" in sentence and "1:05" in sentence, sentence)


def test_a_dead_meshgen_is_one_sentence(workdir):
    section("2b. the picture service not running")
    from forge.tools import model

    md = model.get_props(bpy.context)
    md.image_path = write_png(os.path.join(workdir, "dead.png"))
    point_pref("meshgen_url", "http://127.0.0.1:1")
    try:
        result = bpy.ops.forge.model_generate3d()
        check("the button reports it instead of hanging", "FINISHED" in result,
              str(result))
        check("and the sentence names the fix, not a stack trace",
              md.status_is_error and "Start services" in md.status, md.status)
    finally:
        point_pref("meshgen_url", MESHGEN_URL[0])


# ---------------------------------------------------------------------------
# 4. the health dot
# ---------------------------------------------------------------------------

def test_health_dot(service_url, meshgen_url):
    section("4. the health row's fifth dot")
    from forge.tools import services

    props = services.get_props(bpy.context)
    if not check("scene.forge_services exists", props is not None):
        return
    for name in ("meshgen_state", "meshgen_detail"):
        check("the services props carry %s" % name, hasattr(props, name))

    labels = [row[0] for row in services.rows(props)]
    check("there are five rows now", len(labels) == 5, str(labels))
    check("and Picture to 3D sits with the other services",
          "Picture to 3D" in labels, str(labels))

    MESHGEN_STATE["health"] = "ok"
    result = bpy.ops.forge.services_refresh()
    check("the refresh operator finished", "FINISHED" in result, str(result))
    check("meshgen reads as up", props.meshgen_state == services.UP,
          "%s / %s" % (props.meshgen_state, props.meshgen_detail))
    check("with the backend named in the detail",
          "trellis2" in props.meshgen_detail, props.meshgen_detail)

    MESHGEN_STATE["health"] = "models_missing"
    bpy.ops.forge.services_refresh()
    check("a missing model download is a warning, not a failure",
          props.meshgen_state == services.WARN, props.meshgen_state)
    check("and the detail names the file to fetch",
          "trellis_2_shape_vae" in props.meshgen_detail, props.meshgen_detail)
    MESHGEN_STATE["health"] = "ok"

    point_pref("meshgen_url", "http://127.0.0.1:1")
    bpy.ops.forge.services_refresh()
    check("a service that is not there reads as down",
          props.meshgen_state == services.DOWN, props.meshgen_state)
    check("and the detail says what to press",
          "Start services" in props.meshgen_detail, props.meshgen_detail)
    check("but the row is NOT an error, because this one is optional",
          not props.status_is_error and "Picture to 3D" in props.status,
          "%s / %s" % (props.status, props.status_is_error))
    point_pref("meshgen_url", meshgen_url)
    bpy.ops.forge.services_refresh()
    check("and everything is running again",
          not props.status_is_error and props.meshgen_state == services.UP,
          props.status)


# ---------------------------------------------------------------------------
# 5. the panels still draw
# ---------------------------------------------------------------------------

def test_panels_draw(workdir):
    section("5. the Model box with the new button")
    from forge.tools import assistant, model
    from forge.ui import panels

    md = model.get_props(bpy.context)
    chat = assistant.get_props(bpy.context)

    md.image_path = ""
    chat.image_path = ""
    layout = draw_panel(panels.VIEW3D_PT_forge_model)
    check("the Model box draws the Generate button",
          "forge.model_generate3d" in layout.sink["operators"],
          str(layout.sink["operators"]))
    check("and offers a Picture field when nothing is attached anywhere",
          "image_path" in layout.sink["props"], str(layout.sink["props"]))
    text = " ".join(layout.text().split())
    check("with the five minutes said before the artist presses it",
          "about 5 minutes" in text, text[:200])

    chat.image_path = write_png(os.path.join(workdir, "chatpic.png"))
    layout = draw_panel(panels.VIEW3D_PT_forge_model)
    text = " ".join(layout.text().split())
    check("a picture attached in the chat box is shown as the one it will use",
          "chatpic.png" in text and "Assistant box" in text, text[:200])
    chat.image_path = ""

    md.busy = True
    md.gen_stage = "RemeshMesh (2:05) ..."
    try:
        layout = draw_panel(panels.VIEW3D_PT_forge_model)
        text = " ".join(layout.text().split())
        check("while it runs the stage name is on screen",
              "RemeshMesh" in text, text[:200])
        check("and the bar's honest caveat with it",
              "not the whole job" in text, text[:200])
    finally:
        md.busy = False
        md.gen_stage = ""

    layout = draw_panel(panels.VIEW3D_PT_forge_health)
    text = layout.text()
    check("the health row names all five things",
          all(word in text for word in
              ("Shapes", "Assistant", "Picture to 3D", "Blender link", "Sign-in")),
          text[:200])

    for panel_cls in (panels.VIEW3D_PT_forge_health, panels.VIEW3D_PT_forge_assistant,
                      panels.VIEW3D_PT_forge_model, panels.VIEW3D_PT_forge_partforge,
                      panels.VIEW3D_PT_forge_checks, panels.VIEW3D_PT_forge_segments):
        try:
            draw_panel(panel_cls)
            ok, detail = True, ""
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            ok, detail = False, str(exc)
        check("%s draws" % panel_cls.bl_idname, ok, detail)


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

MESHGEN_URL = [""]


def main():
    print("Forge add-on meshgen (Phase 7) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()

    service, service_url = start_fake(_FakeService)
    meshgen, meshgen_url = start_fake(_FakeMeshgen)
    MESHGEN_URL[0] = meshgen_url
    note("fake geometry service on %s" % service_url)
    note("fake meshgen on %s" % meshgen_url)
    point_pref("service_url", service_url)
    point_pref("meshgen_url", meshgen_url)

    workdir = tempfile.mkdtemp(prefix="forge_meshgen_")
    _TEMP.append(workdir)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        test_import_generated_round_trip(workdir)
        test_import_generated_refusals(workdir)
        test_generate_button(workdir)
        test_a_dead_meshgen_is_one_sentence(workdir)
        test_health_dot(service_url, meshgen_url)
        test_panels_draw(workdir)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for httpd in (service, meshgen):
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        import shutil

        for directory in _TEMP:
            shutil.rmtree(directory, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
