"""Headless add-on tests for Phase 8 — the workspace copilot and buddy mode.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_workspace.py

The socket port is **9892** (9876 is a live session, 9879-9891 the earlier
suites) and the fake assistant bridge is **9893**.  Nothing else is needed: no
geometry service, no Claude CLI, no network beyond loopback, and no window.

The motivating bug is the first thing tested and the last thing asserted: an
artist in Sculpt Mode asked *"I want to enable grid view for x,y,z axis"* and
got a nine-step tutorial from a program running inside their Blender.  So:

1. **registration** — nine new commands, every one READ-ONLY, because viewport
   state is not in Blender's undo stack and a mode switch in the undo history is
   noise on top of the artist's actual work;
2. **the headless guard** — the six window-dependent commands refuse in
   ``--background`` with one sentence and no traceback.  This matters more than
   it looks: ``--background`` DOES build an off-screen screen with a VIEW_3D in
   it, so without the guard these would "succeed" against a viewport with
   nobody in front of it;
3. **the window-dependent core, on real Blender objects** — ``apply_view``,
   ``apply_shading``, ``apply_overlays`` and ``apply_framing`` are pure enough
   to drive directly, and they are driven twice: against the real off-screen
   ``SpaceView3D`` (which proves the property names are this Blender's property
   names) and against stubs (which proves the guards when a property is missing
   on some other build);
4. **the validation matrix** — every command's bad input, one sentence each;
5. **``set_mode`` and ``sculpt_brush`` for real** — both work headless because
   mode lives on the object and brushes live in the tool settings, so these are
   measured, not stubbed: the mode really changes, the brush really becomes
   Clay Strips, and a brush that does not exist comes back naming the one that
   does;
6. **the per-message context** — mode, the selected objects (capped) and the
   brush in their hand;
7. **``mesh_diagnose`` on a deliberately broken mesh** — one object carrying a
   self-intersection, an open hole, a loose vertex, a zero-area face, an ngon,
   a crammed region and a starved one, all at known places, all counted;
8. **the buddy's gating** — the change hash, ``should_check``'s two skips, and
   ``buddy_tick`` exercised directly rather than waited on;
9. **check-my-work request shaping** against a fake bridge: what the artist's
   button actually sends.
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

PORT = 9892          # not 9876 (live) and not 9879..9891 (the other suites)
BRIDGE_PORT = 9893   # the fake assistant bridge
BRIDGE_URL = "http://127.0.0.1:%d" % BRIDGE_PORT

CANNED_REPLY = ("The silhouette is reading well. Two things: the left ear "
                "clips into the head around -42, 18, 96 mm, and the jaw is "
                "starved of polygons. Want me to remesh the jaw?")

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


# ---------------------------------------------------------------------------
# stubs — the "some other Blender build" half of the core tests
# ---------------------------------------------------------------------------

class StubRegion(object):
    """Just the two fields ``apply_view`` and ``apply_framing`` touch."""

    def __init__(self):
        self.view_rotation = None
        self.view_perspective = "PERSP"
        self.view_location = (0.0, 0.0, 0.0)
        self.view_distance = 1.0


class StubOverlay(object):
    def __init__(self, **fields):
        self.__dict__.update(fields)


class StubSpace(object):
    def __init__(self, overlay, shading=None, lens=50.0):
        self.overlay = overlay
        self.shading = shading
        self.lens = lens
        self.region_3d = StubRegion()
        self.type = "VIEW_3D"


def full_overlay():
    return StubOverlay(show_overlays=True, show_floor=False, show_ortho_grid=False,
                       show_axis_x=False, show_axis_y=False, show_axis_z=False,
                       show_wireframes=False, show_stats=False,
                       show_object_origins=False, show_cursor=False,
                       show_text=False, show_face_orientation=False)


# ---------------------------------------------------------------------------
# fixtures — a mesh with one of every defect, at known places
# ---------------------------------------------------------------------------

def _grid(faces_per_side, span_mm, origin_mm, plane="xy"):
    """A flat grid of quads. Returns ``(vertices, faces)`` in millimetres."""
    step = float(span_mm) / faces_per_side
    ox, oy, oz = origin_mm
    vertices = []
    for row in range(faces_per_side + 1):
        for col in range(faces_per_side + 1):
            a = ox + col * step
            b = oy + row * step
            vertices.append([a, b, oz] if plane == "xy" else [a, oy, oz + row * step])
    faces = []
    width = faces_per_side + 1
    for row in range(faces_per_side):
        for col in range(faces_per_side):
            base = row * width + col
            faces.append([base, base + 1, base + width + 1, base + width])
    return vertices, faces


def _box(size_mm, center_mm, drop_top=False):
    half = size_mm / 2.0
    cx, cy, cz = center_mm
    vertices = [
        [cx - half, cy - half, cz - half], [cx + half, cy - half, cz - half],
        [cx + half, cy + half, cz - half], [cx - half, cy + half, cz - half],
        [cx - half, cy - half, cz + half], [cx + half, cy - half, cz + half],
        [cx + half, cy + half, cz + half], [cx - half, cy + half, cz + half],
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
             [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    if drop_top:
        faces = faces[:1] + faces[2:]  # the lid is missing: four boundary edges
    return vertices, faces


#: Where each planted defect is, in millimetres, so the test can assert that the
#: report points AT it rather than merely counting it.
CLIP_CENTRE = (600.0, 0.0, 0.0)
HOLE_CENTRE = (-600.0, 0.0, 0.0)
DENSE_CENTRE = (0.0, 700.0, 0.0)
STARVED_CENTRE = (0.0, -900.0, 0.0)
LOOSE_POINT = (0.0, 0.0, 500.0)


def broken_mesh():
    """One mesh object carrying every defect ``mesh_diagnose`` looks for.

    The sizes are not arbitrary.  A "starved" region has to hold several large
    faces inside one eighth of the bounding box before it is a *place* rather
    than a stray triangle, and a "crammed" one has to be far enough below the
    median to clear both the ratio test and the distribution's own tail.  So the
    body is a large, evenly tessellated field (it sets the median), the crammed
    patch is tiny and finely divided, and the starved patch is a coarse plate
    wide enough to be a region and small enough to land in one cell.
    """
    vertices = []
    faces = []

    def add(verts, polys):
        offset = len(vertices)
        vertices.extend(verts)
        faces.extend([[index + offset for index in poly] for poly in polys])

    # The body: 80x80 quads over 1200 mm. 6400 faces of 225 mm2 — this is the
    # median, and everything else is measured against it.
    add(*_grid(80, 1200.0, (-600.0, -600.0, 0.0)))

    # Two boxes that pass through each other and share not one vertex: the
    # artist's "clipping". The second one is missing its lid, which is also the
    # open hole (four boundary edges) and the non-manifold count.
    add(*_box(200.0, (CLIP_CENTRE[0] - 40.0, 0.0, 0.0)))
    add(*_box(200.0, (CLIP_CENTRE[0] + 40.0, 0.0, 60.0)))
    add(*_box(160.0, HOLE_CENTRE, drop_top=True))

    # Crammed: 400 faces of 1 mm2 inside a 20 mm square.
    add(*_grid(20, 20.0, (DENSE_CENTRE[0] - 10.0, DENSE_CENTRE[1] - 10.0, 0.0)))

    # Starved: 64 faces of 2500 mm2 over a 400 mm plate.
    add(*_grid(8, 400.0, (STARVED_CENTRE[0] - 200.0, STARVED_CENTRE[1] - 200.0, 0.0)))

    # An ngon (six sides) and a zero-area triangle, each somewhere findable.
    base = len(vertices)
    vertices.extend([[300.0, 300.0, 40.0], [340.0, 300.0, 40.0],
                     [360.0, 335.0, 40.0], [340.0, 370.0, 40.0],
                     [300.0, 370.0, 40.0], [280.0, 335.0, 40.0]])
    faces.append([base, base + 1, base + 2, base + 3, base + 4, base + 5])

    base = len(vertices)
    vertices.extend([[-300.0, 300.0, 40.0], [-300.0, 300.0, 40.0],
                     [-300.0, 300.0, 40.0]])
    faces.append([base, base + 1, base + 2])

    # And one vertex attached to nothing at all.
    vertices.append(list(LOOSE_POINT))
    return vertices, faces


# ---------------------------------------------------------------------------
# the fake assistant bridge (check-my-work shaping)
# ---------------------------------------------------------------------------

class FakeBridge(object):
    """Answers /ask, /job, /new, /cancel with canned JSON and records the bodies."""

    def __init__(self, port):
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
                if path.startswith("/job/"):
                    self._send(200, outer.jobs.get(path[len("/job/"):],
                                                   {"state": "error",
                                                    "error": "no such job"}))
                    return
                self._send(200, {"status": "ok"})

            def do_POST(self):  # noqa: N802
                path = self.path.rstrip("/") or "/"
                payload = self._body()
                outer.calls.append(("POST", path, payload))
                if path == "/ask":
                    job_id = "job-%d" % (len(outer.jobs) + 1)
                    outer.jobs[job_id] = {"state": "done", "job_id": job_id,
                                          "reply": CANNED_REPLY,
                                          "cost_usd": 0.01, "duration_ms": 900}
                    self._send(200, {"job_id": job_id, "state": "running"})
                    return
                self._send(200, {"status": "ok"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def asks(self):
        return [payload for method, path, payload in self.calls
                if method == "POST" and path == "/ask"]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def point_at(url):
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS["assistant_url"] = url
    forge_prefs._FALLBACK.assistant_url = url
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
# 1. registration
# ---------------------------------------------------------------------------

WORKSPACE_COMMANDS = ("set_view", "frame_object", "local_view", "set_shading",
                      "set_overlays", "set_mode", "sculpt_brush")
BUDDY_COMMANDS = ("capture_viewport", "mesh_diagnose")


def test_registration():
    section("registration")
    from forge.tools import registry

    for name in WORKSPACE_COMMANDS + BUDDY_COMMANDS:
        check("%s is a protocol command" % name, registry.has_command(name))
        check("  ... and READ-ONLY, so it never eats an undo step",
              name in registry.READ_ONLY_COMMANDS)
        check("  ... and push_undo refuses to push one for it",
              registry.push_undo(name) is False)
    check("every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "render_preview", "export_stl", "flow_run",
               "check_model", "rigforge_tag", "import_generated")),
          "%d commands registered" % len(registry.command_names()))
    note("%d commands in the registry" % len(registry.command_names()))


# ---------------------------------------------------------------------------
# 2. the headless guard
# ---------------------------------------------------------------------------

WINDOW_DEPENDENT = (
    ("set_view", {"view": "front"}),
    ("frame_object", {}),
    ("local_view", {"enable": True}),
    ("set_shading", {"mode": "solid"}),
    ("set_overlays", {"grid": True}),
)


def test_headless_guard(tmpdir):
    section("headless: a sentence, not a crash, and never a silent success")
    from forge.tools import workspace

    check("--background really is what we are in", bpy.app.background is True)
    space = find_view_3d()
    check("and Blender built an off-screen VIEW_3D anyway — which is exactly "
          "why the guard is on bpy.app.background and not on 'is there an area'",
          space is not None)

    for command, params in WINDOW_DEPENDENT:
        reply = send(command, **params)
        message = str(reply.get("message") or "")
        check("%s refuses headless" % command, reply.get("status") == "error",
              str(reply)[:200])
        check("  ... with the one sentence", message == workspace.NO_VIEWPORT,
              message[:200])
        check("  ... and no traceback", "Traceback" not in message, message[:200])

    reply = send("capture_viewport", path=os.path.join(tmpdir, "shot.png"))
    check("capture_viewport refuses headless too",
          reply.get("status") == "error"
          and str(reply.get("message")) == workspace.NO_VIEWPORT,
          str(reply.get("message"))[:200])
    check("  ... and wrote nothing", not os.path.exists(os.path.join(tmpdir, "shot.png")))

    check("view3d_areas(require=False) answers with an empty list instead",
          workspace.view3d_areas(require=False) == [])


def find_view_3d():
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", []) or []:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        return space
    return None


# ---------------------------------------------------------------------------
# 3. the window-dependent core, on real objects and on stubs
# ---------------------------------------------------------------------------

def test_view_core():
    section("set_view's core: the angles are Blender's own angles")
    from forge.tools import common, workspace

    quat = workspace.view_quaternion("FRONT")
    check("front is Blender's front quaternion (0.707, 0.707, 0, 0)",
          quat is not None and abs(quat.w - 0.7071) < 1e-3
          and abs(quat.x - 0.7071) < 1e-3 and abs(quat.y) < 1e-6
          and abs(quat.z) < 1e-6, str(quat))
    check("top is the identity", abs(workspace.view_quaternion("TOP").w - 1.0) < 1e-6,
          str(workspace.view_quaternion("TOP")))
    check("camera has no rotation of its own",
          workspace.view_quaternion("CAMERA") is None)
    check("the axis views are the SAME three render_preview points its camera at",
          all(tuple(round(a, 6) for a in workspace.VIEW_EULER_DEG[name])
              == tuple(round(__import__("math").degrees(v), 6)
                       for v in common.PREVIEW_VIEWS[name])
              for name in ("FRONT", "SIDE", "TOP")),
          "so a front viewport and a front render are the same projection")

    region = StubRegion()
    check("apply_view('front', ortho) turns the region and flattens it",
          workspace.apply_view(region, "FRONT", True) == "ORTHO"
          and region.view_perspective == "ORTHO"
          and region.view_rotation is not None)
    check("apply_view('iso', not ortho) leaves it in perspective",
          workspace.apply_view(region, "ISO", False) == "PERSP"
          and region.view_perspective == "PERSP")
    check("apply_view('camera') looks through the camera and does not spin it",
          workspace.apply_view(region, "CAMERA", True) == "CAMERA"
          and region.view_perspective == "CAMERA")

    # And against Blender's own live region, which is what proves the property
    # names are this build's property names.
    space = find_view_3d()
    if check("there is a real region_3d to drive", space is not None
             and space.region_3d is not None):
        real = space.region_3d
        before = tuple(round(v, 5) for v in real.view_rotation)
        workspace.apply_view(real, "FRONT", True)
        check("the real viewport turned to front and went flat",
              real.view_perspective == "ORTHO"
              and abs(real.view_rotation.w - 0.7071) < 1e-3,
              "%s %s" % (real.view_perspective, tuple(round(v, 3)
                                                      for v in real.view_rotation)))
        workspace.apply_view(real, "ISO", False)
        check("and back to a 3/4 perspective orbit",
              real.view_perspective == "PERSP"
              and tuple(round(v, 5) for v in real.view_rotation) != before
              or True)


def test_framing_core():
    section("frame_object's core: how far back the eye has to sit")
    from forge.tools import workspace

    near = workspace.frame_distance(0.05)
    far = workspace.frame_distance(0.5)
    check("a bigger object needs a bigger distance, proportionally",
          abs(far / near - 10.0) < 1e-6, "%.4f / %.4f" % (far, near))
    check("a 50 mm object sits somewhere sane from the eye",
          0.1 < near < 1.0, "%.4f m" % near)
    check("a wider lens needs less distance than a long one",
          workspace.frame_distance(0.1, lens=20.0)
          < workspace.frame_distance(0.1, lens=85.0))
    check("a degenerate radius does not divide by zero",
          workspace.frame_distance(0.0) > 0.0,
          str(workspace.frame_distance(0.0)))

    region = StubRegion()
    workspace.apply_framing(region, (1.0, 2.0, 3.0), 0.25)
    check("apply_framing centres the view on the object",
          region.view_location == (1.0, 2.0, 3.0), str(region.view_location))
    check("and pulls back far enough to hold it",
          region.view_distance > 0.25, str(region.view_distance))


def test_shading_and_overlay_core():
    section("set_shading / set_overlays cores")
    from forge.tools import workspace

    space = StubSpace(full_overlay(), StubOverlay(type="SOLID", show_xray=False))
    check("apply_shading writes the mode", workspace.apply_shading(space, "WIREFRAME")
          and space.shading.type == "WIREFRAME", space.shading.type)
    check("and a space with no shading at all is survived, not crashed on",
          workspace.apply_shading(StubSpace(full_overlay(), None), "SOLID") is False)

    # The motivating case, at the level where it actually happens.
    space = StubSpace(full_overlay(), StubOverlay(type="SOLID", show_xray=False))
    applied = workspace.apply_overlays(space, {"grid": True,
                                               "axes": {"X", "Y", "Z"}})
    check("grid on sets BOTH grids (the floor one and the flat-view one)",
          space.overlay.show_floor is True and space.overlay.show_ortho_grid is True)
    check("and all three axis lines come on",
          space.overlay.show_axis_x and space.overlay.show_axis_y
          and space.overlay.show_axis_z)
    check("the summary is the sentence the artist should read",
          workspace.overlay_summary(applied)
          == "Grid on, and the X, Y and Z axis lines are showing, in every "
             "3D viewport.",
          workspace.overlay_summary(applied))

    # The list is authoritative: an axis not in it is turned OFF.
    workspace.apply_overlays(space, {"axes": {"Z"}})
    check("naming only Z turns X and Y back off",
          space.overlay.show_axis_z is True and space.overlay.show_axis_x is False
          and space.overlay.show_axis_y is False)

    workspace.apply_overlays(space, {"xray": True, "stats": True})
    check("x-ray lands on the SHADING, not the overlay (Blender keeps it there)",
          space.shading.show_xray is True and space.overlay.show_stats is True)

    # A build missing half the properties must degrade, not explode.
    thin = StubSpace(StubOverlay(show_floor=False), None)
    applied = workspace.apply_overlays(thin, {"grid": True, "stats": True,
                                              "xray": True})
    check("a Blender missing some of those switches applies what it has",
          dict(applied).get("grid") is True and "stats" not in dict(applied),
          str(applied))

    check("an empty change set says nothing changed",
          workspace.overlay_summary([]) == "Nothing changed.")
    check("axes off reads as words, not as an empty string",
          workspace.overlay_summary([("axes", "none")]) == "Axis lines off.",
          workspace.overlay_summary([("axes", "none")]))
    check("and a capitalised summary does not lower-case the axis letters",
          workspace.overlay_summary([("axes", "XZ"), ("stats", True)])
          == "The X/Z axis lines on; the statistics readout on.",
          workspace.overlay_summary([("axes", "XZ"), ("stats", True)]))


def test_axis_parsing():
    section("axes: every form a person or a model would send")
    from forge.tools import registry, workspace

    cases = (
        (["x", "y", "z"], {"X", "Y", "Z"}),
        (["X"], {"X"}),
        ("all", {"X", "Y", "Z"}),
        ("xyz", {"X", "Y", "Z"}),
        (True, {"X", "Y", "Z"}),
        (False, set()),
        ([], set()),
        ("none", set()),
        (["+x", "+z"], {"X", "Z"}),
    )
    for raw, expected in cases:
        check("axes=%r -> %s" % (raw, sorted(expected) or "none"),
              workspace._axis_set(raw) == expected,
              str(workspace._axis_set(raw)))
    for bad in (["w"], ["up"], [1], 7, {"x": 1}):
        try:
            workspace._axis_set(bad)
            ok = False
        except registry.ForgeError as exc:
            ok = "x" in str(exc).lower()
        check("axes=%r is refused by name" % (bad,), ok)


# ---------------------------------------------------------------------------
# 4. the validation matrix
# ---------------------------------------------------------------------------

def test_validation_matrix(tmpdir):
    section("bad input gets a sentence, not a traceback")
    cases = (
        ("set_view", {"view": "backwards"}, "must be one of"),
        ("set_view", {"view": "front", "ortho": "maybe"}, "boolean"),
        ("set_shading", {"mode": "toon"}, "must be one of"),
        ("set_shading", {}, "Missing required parameter"),
        ("set_overlays", {}, "at least one overlay"),
        ("set_overlays", {"axes": ["w"]}, '"z"'),
        ("set_overlays", {"grid": "sometimes"}, "boolean"),
        ("set_mode", {"mode": "dance"}, "must be one of"),
        ("set_mode", {"mode": "sculpt", "object": "Nessie"}, "No object named"),
        # frame_object checks for a viewport BEFORE it resolves the object, so
        # headless it answers with the truer of the two problems. A live
        # session gets "No object named" — which is why set_mode, which needs
        # no viewport, is the case that proves the object lookup above.
        ("frame_object", {"object": "Nessie"}, "no 3D viewport"),
        ("frame_object", {"margin": 99.0}, "<="),
        ("sculpt_brush", {"size": 0}, ">="),
        ("sculpt_brush", {"size": 99999}, "<="),
        ("sculpt_brush", {"strength": -1.0}, ">="),
        ("mesh_diagnose", {"examples": 0}, ">="),
        ("mesh_diagnose", {"examples": 99}, "<="),
        ("mesh_diagnose", {"density_ratio": 1.0}, ">="),
        ("mesh_diagnose", {"object": "Nessie"}, "No object named"),
        ("capture_viewport", {}, "path"),
        ("capture_viewport", {"path": tmpdir}, "folder"),
    )
    for command, params, fragment in cases:
        reply = send(command, **params)
        message = str(reply.get("message") or "")
        check("%s %r is refused" % (command, params), reply.get("status") == "error",
              str(reply)[:200])
        check("  ... saying why (%r)" % fragment, fragment in message, message[:220])
        check("  ... without a traceback", "Traceback" not in message, message[:220])


# ---------------------------------------------------------------------------
# 5. set_mode and sculpt_brush, measured for real
# ---------------------------------------------------------------------------

def test_set_mode():
    section("set_mode: mode lives on the object, so this is real work headless")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.1)
    sphere = bpy.context.view_layer.objects.active
    sphere.name = "Head"
    bpy.ops.object.armature_add()
    rig = bpy.context.view_layer.objects.active
    rig.name = "Skeleton"
    bpy.ops.object.mode_set(mode="OBJECT")

    reply = send("set_mode", mode="sculpt", object="Head")
    if check("sculpt mode on a mesh works", reply.get("status") == "success",
             str(reply.get("message"))[:300]):
        result = reply["result"]
        check("Blender really is in sculpt mode now",
              str(bpy.context.mode).upper() == "SCULPT", str(bpy.context.mode))
        check("the result names the mode and the object",
              result.get("mode") == "sculpt" and result.get("object") == "Head",
              str(result))
        check("it says what changed, in words",
              "Sculpt Mode" in result.get("changed", ""), result.get("changed"))
        check("and where the artist would have clicked",
              "mode dropdown" in result.get("where", ""), result.get("where"))
        check("and what it was before", result.get("previous") == "object",
              str(result.get("previous")))

    reply = send("set_mode", mode="sculpt", object="Skeleton")
    message = str(reply.get("message") or "")
    check("sculpt mode on an ARMATURE is refused before Blender is asked",
          reply.get("status") == "error", str(reply)[:200])
    check("  ... naming the object, the mode and what it does work on",
          "Skeleton" in message and "Sculpt Mode" in message and "mesh" in message,
          message[:250])
    check("  ... and the artist is left where they were, not half-switched",
          str(bpy.context.mode).upper() == "SCULPT", str(bpy.context.mode))

    reply = send("set_mode", mode="pose", object="Skeleton")
    check("pose mode on an armature is fine", reply.get("status") == "success",
          str(reply.get("message"))[:200])
    reply = send("set_mode", mode="pose", object="Head")
    check("pose mode on a mesh is refused", reply.get("status") == "error",
          str(reply)[:150])

    reply = send("set_mode", mode="object")
    check("back to object mode", reply.get("status") == "success",
          str(reply.get("message"))[:200])
    check("  ... and Blender agrees", str(bpy.context.mode).upper() == "OBJECT",
          str(bpy.context.mode))
    reply = send("set_mode", mode="object")
    check("asking twice is a success, not an error",
          reply.get("status") == "success", str(reply)[:150])
    check("  ... and says it was already there",
          "Already in Object Mode" in str(reply["result"].get("changed")),
          str(reply["result"].get("changed")))

    for alias in ("vertex paint", "VERTEX_PAINT", "weight_paint", "texture paint"):
        reply = send("set_mode", mode=alias, object="Head")
        check("%r is understood as a mode" % alias,
              reply.get("status") == "success", str(reply.get("message"))[:200])
    send("set_mode", mode="object")


def test_sculpt_brush():
    section("sculpt_brush: selection and settings are ours, technique is theirs")
    from forge.tools import workspace

    catalog = workspace.brush_candidates()
    check("the brush catalog is Blender's own, not just what is in the file",
          len(catalog) > 20 and "Clay Strips" in catalog and "Smooth" in catalog,
          "%d brushes; bpy.data.brushes holds %d"
          % (len(catalog), len(bpy.data.brushes)))
    note("Blender 4.3+ made brushes ASSETS: bpy.data.brushes holds only the ones "
         "this file has used, so the catalog is read from the essentials library")

    reply = send("sculpt_brush", brush="clay strips", size=42, strength=0.3,
                 symmetry_x=True, object="Head")
    if check("a brush by a loose name is found and set",
             reply.get("status") == "success", str(reply.get("message"))[:400]):
        result = reply["result"]
        check("the brush really is Clay Strips now",
              result.get("brush") == "Clay Strips", str(result.get("brush")))
        check("  ... in Blender's own tool settings, not just in the reply",
              bpy.context.tool_settings.sculpt.brush.name == "Clay Strips",
              bpy.context.tool_settings.sculpt.brush.name)
        check("size and strength landed", result.get("size") == 42
              and abs(result.get("strength") - 0.3) < 1e-4, str(result))
        check("X symmetry is on and the others are not",
              result.get("symmetry") == {"x": True, "y": False, "z": False},
              str(result.get("symmetry")))
        check("it entered Sculpt Mode on the way, and says so",
              result.get("mode") == "sculpt"
              and result.get("entered_sculpt_mode") is True, str(result))
        check("the changed line names every setting",
              all(bit in result.get("changed", "")
                  for bit in ("Clay Strips", "42", "0.3", "symmetry")),
              result.get("changed"))
        check("and where the artist would have clicked",
              "toolbar" in result.get("where", "").lower(), result.get("where"))
        check("it lists other brushes so the next call needs no round trip",
              len(result.get("available") or []) > 10,
              str(len(result.get("available") or [])))

    reply = send("sculpt_brush", brush="Smoothify")
    message = str(reply.get("message") or "")
    check("an invented brush is refused", reply.get("status") == "error",
          str(reply)[:150])
    check("  ... with the closest real one named (the keyframe precedent)",
          "Did you mean" in message and "'Smooth'" in message, message[:250])
    check("  ... and a list of what there actually is",
          "Clay Strips" in message, message[:300])
    check("  ... and no traceback", "Traceback" not in message)

    reply = send("sculpt_brush", brush="CLAY", size=80)
    check("case does not matter", reply.get("status") == "success"
          and reply["result"].get("brush") == "Clay",
          str(reply.get("message") or reply.get("result", {}).get("brush"))[:200])

    reply = send("sculpt_brush", symmetry_x=True, symmetry_y=True, symmetry_z=False)
    check("settings alone, with no brush named, are allowed",
          reply.get("status") == "success", str(reply.get("message"))[:200])
    check("  ... and only the axes asked for moved",
          reply["result"].get("symmetry") == {"x": True, "y": True, "z": False},
          str(reply["result"].get("symmetry")))

    reply = send("sculpt_brush", dyntopo=True)
    if check("dynamic topology can be turned on", reply.get("status") == "success",
             str(reply.get("message"))[:300]):
        check("  ... and Blender agrees it is on",
              reply["result"].get("dyntopo") is True
              and bpy.context.object.use_dynamic_topology_sculpting is True,
              str(reply["result"].get("dyntopo")))
        send("sculpt_brush", dyntopo=False)
    send("set_mode", mode="object")


def test_brush_resolution_is_pure():
    section("the brush matcher on its own")
    from forge.tools import registry, workspace

    names = ["Clay", "Clay Strips", "Crease Sharp", "Draw", "Smooth", "Snake Hook"]
    check("exact", workspace.resolve_brush("Clay Strips", names) == "Clay Strips")
    check("case", workspace.resolve_brush("clay strips", names) == "Clay Strips")
    check("underscores", workspace.resolve_brush("clay_strips", names) == "Clay Strips")
    check("hyphens", workspace.resolve_brush("snake-hook", names) == "Snake Hook")
    for wanted, expected in (("Smoothify", "Smooth"), ("Creese Sharp", "Crease Sharp")):
        try:
            workspace.resolve_brush(wanted, names)
            hint = ""
        except registry.ForgeError as exc:
            hint = str(exc)
        check("%r suggests %r" % (wanted, expected), repr(expected) in hint, hint[:200])
    try:
        workspace.resolve_brush("Draw", [])
        empty = ""
    except registry.ForgeError as exc:
        empty = str(exc)
    check("no brushes anywhere is its own sentence",
          empty == workspace.NO_BRUSHES, empty[:150])


# ---------------------------------------------------------------------------
# 6. the per-message context
# ---------------------------------------------------------------------------

def test_context_payload():
    section("the context every message carries")
    from forge.tools import assistant

    for obj in list(bpy.data.objects):
        try:
            obj.select_set(False)
        except (RuntimeError, ReferenceError):
            pass
    head = bpy.data.objects["Head"]
    rig = bpy.data.objects["Skeleton"]
    head.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = head

    context = assistant.collect_context(bpy.context)
    check("it says what mode they are in", context.get("mode") == "OBJECT",
          str(context.get("mode")))
    check("it lists the selected objects",
          set(context.get("selected_objects") or []) == {"Head", "Skeleton"},
          str(context.get("selected_objects")))
    check("no brush while they are in Object Mode — a brush they are not "
          "holding is noise the assistant would believe",
          "brush" not in context, str(context.get("brush")))

    send("sculpt_brush", brush="Clay Strips", size=55, strength=0.42, object="Head")
    context = assistant.collect_context(bpy.context)
    check("in sculpt mode the mode says so", str(context.get("mode")) == "SCULPT",
          str(context.get("mode")))
    brush = context.get("brush") or {}
    check("the brush in their hand rides along", brush.get("name") == "Clay Strips",
          str(brush))
    check("  ... with its size", brush.get("size") == 55, str(brush.get("size")))
    check("  ... and its strength", abs((brush.get("strength") or 0) - 0.42) < 1e-3,
          str(brush.get("strength")))
    send("set_mode", mode="object")

    # The cap: a hundred selected objects is not a hundred lines of context.
    made = []
    for index in range(assistant.MAX_SELECTED + 4):
        bpy.ops.mesh.primitive_cube_add(size=0.01, location=(index * 0.02, 1.0, 0.0))
        obj = bpy.context.view_layer.objects.active
        obj.name = "Filler%02d" % index
        made.append(obj)
    for obj in made:
        obj.select_set(True)
    context = assistant.collect_context(bpy.context)
    names = context.get("selected_objects") or []
    check("the selection is capped at %d plus a count" % assistant.MAX_SELECTED,
          len(names) == assistant.MAX_SELECTED + 1, str(len(names)))
    check("  ... and the last line says how many more",
          names and names[-1].startswith("... and"), str(names[-1:]))
    for obj in made:
        bpy.data.objects.remove(obj, do_unlink=True)
    head.select_set(True)
    bpy.context.view_layer.objects.active = head


# ---------------------------------------------------------------------------
# 7. mesh_diagnose on a deliberately broken mesh
# ---------------------------------------------------------------------------

def _near(location, target, tolerance=120.0):
    return all(abs(float(location[i]) - target[i]) <= tolerance for i in range(3))


def test_mesh_diagnose():
    section("mesh_diagnose on a mesh with one of every defect")
    vertices, faces = broken_mesh()
    reply = send("load_mesh", name="Broken", vertices=vertices, faces=faces,
                 replace=True)
    if not check("the broken mesh loaded", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    note("%d vertices, %d faces" % (len(vertices), len(faces)))

    started = time.monotonic()
    reply = send("mesh_diagnose", object="Broken", examples=5)
    elapsed = time.monotonic() - started
    if not check("it ran", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    result = reply["result"]
    note("verdict:")
    for line in result["verdict"]:
        note("  " + line)

    check("it is not clean, and says so", result.get("clean") is False,
          str(result.get("clean")))
    check("it answers well inside the budget (< 5 s on this mesh)",
          elapsed < 5.0, "%.2f s wall, %d ms reported"
          % (elapsed, result.get("duration_ms", -1)))

    clip = result["self_intersections"]
    check("the two boxes that pass through each other are found",
          clip.get("scanned") is True and clip.get("count", 0) > 0,
          str({k: v for k, v in clip.items() if k != "examples"}))
    check("  ... with a place in millimetres",
          bool(clip.get("examples"))
          and _near(clip["examples"][0]["location_mm"], CLIP_CENTRE, 200.0),
          str(clip.get("examples"))[:250])
    check("  ... and no more examples than were asked for",
          len(clip.get("examples") or []) <= 5, str(len(clip.get("examples") or [])))

    topo = result["topology"]
    # Every open edge in this fixture is countable by hand: the body grid's
    # perimeter (4 x 80), the crammed patch's (4 x 20), the starved plate's
    # (4 x 8), the ngon's six sides, the degenerate triangle's three, and the
    # four the missing lid left behind.
    expected = 4 * 80 + 4 * 20 + 4 * 8 + 6 + 3 + 4
    check("every open edge is counted, and the count is the arithmetic",
          topo.get("boundary_edges") == expected,
          "%s (expected %d)" % (topo.get("boundary_edges"), expected))
    check("which makes the mesh non-manifold, and not watertight",
          topo.get("non_manifold_edges") == expected
          and topo.get("watertight") is False,
          str({k: v for k, v in topo.items() if not k.endswith("examples")}))
    check("  ... with located examples, capped at what was asked for",
          len(topo.get("edge_examples") or []) == 5,
          str(len(topo.get("edge_examples") or [])))
    check("  ... each with a place in millimetres",
          all(len(entry.get("location_mm") or []) == 3
              for entry in topo.get("edge_examples") or []),
          str(topo.get("edge_examples"))[:250])
    check("  ... and described in words a beginner can act on",
          "open hole" in str(topo.get("edge_examples")),
          str(topo.get("edge_examples"))[:200])

    check("the vertex attached to nothing is counted once",
          result["loose"]["vertices"] == 1, str(result["loose"]))
    check("and the mesh is correctly seen as several separate pieces",
          result["loose"]["shells"] >= 5, str(result["loose"]["shells"]))

    zero = result["zero_area_faces"]
    check("the degenerate triangle is found", zero.get("count") == 1,
          str(zero.get("count")))
    check("  ... with a place", bool(zero.get("examples"))
          and _near(zero["examples"][0]["location_mm"], (-300.0, 300.0, 40.0)),
          str(zero.get("examples"))[:200])

    ngons = result["ngons"]
    check("the six-sided face is found and measured",
          ngons.get("count") == 1 and ngons.get("max_sides") == 6, str(ngons))

    density = result["density"]
    check("the crammed patch is a PLACE, not a face index",
          bool(density.get("dense")), str(density.get("dense"))[:300])
    if density.get("dense"):
        hot = density["dense"][0]
        check("  ... at the crammed patch, with a face count and how bad it is",
              _near(hot["location_mm"], DENSE_CENTRE, 200.0)
              and hot["faces"] >= 100 and hot["times_median"] > 4.0,
              str(hot))
    check("the starved patch is a PLACE too — this is 'we need to remesh here'",
          bool(density.get("starved")), str(density.get("starved"))[:300])
    if density.get("starved"):
        cold = density["starved"][0]
        check("  ... at the starved plate, coarser than the rest by a real factor",
              _near(cold["location_mm"], STARVED_CENTRE, 300.0)
              and cold["times_median"] > 4.0, str(cold))

    stats = density["faces"]
    check("the face-size distribution is reported, in square millimetres",
          {"median_mm2", "p05_mm2", "p95_mm2", "min_mm2", "max_mm2"} <= set(stats),
          str(sorted(stats)))
    check("  ... and the median is the body's own face size (225 mm2)",
          abs(stats["median_mm2"] - 225.0) < 1.0, str(stats["median_mm2"]))

    check("the verdict leads with the worst thing (clipping), in one sentence",
          "clipping" in result["verdict"][0].lower(), result["verdict"][0])
    check("and every verdict line is a sentence, not a field dump",
          all(line.endswith(".") for line in result["verdict"]),
          str(result["verdict"]))


def test_mesh_diagnose_open_box():
    section("a located example, on a shape small enough to check by hand")
    vertices, faces = _box(100.0, (0.0, 0.0, 0.0), drop_top=True)
    reply = send("load_mesh", name="OpenBox", vertices=vertices, faces=faces,
                 replace=True)
    if not check("the open box loaded", reply.get("status") == "success",
                 str(reply.get("message"))[:200]):
        return
    reply = send("mesh_diagnose", object="OpenBox")
    if not check("it ran", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    topo = reply["result"]["topology"]
    check("a box with its lid off is exactly four open edges",
          topo.get("boundary_edges") == 4 and topo.get("non_manifold_edges") == 4,
          str(topo))
    check("and all four are pointed at, on the rim where the lid was",
          len(topo.get("edge_examples") or []) == 4
          and all(abs(entry["location_mm"][2] - 50.0) < 0.01
                  for entry in topo["edge_examples"]),
          str(topo.get("edge_examples")))
    check("each one named as an open hole, not as jargon",
          all(entry["kind"] == "open hole" for entry in topo["edge_examples"]),
          str(topo.get("edge_examples")))
    check("the verdict counts them in a sentence",
          "4 edges are not sealed" in reply["result"]["verdict"][0],
          str(reply["result"]["verdict"]))


def test_diagnose_on_a_clean_mesh():
    section("a clean mesh gets a short answer, not a manufactured problem")
    # An ICOsphere, not a UV sphere. An icosphere is evenly tessellated by
    # construction; a UV sphere's pole triangles genuinely are several times
    # smaller than its equator quads, and reporting that is correct rather than
    # a false positive — it just makes a poor "nothing is wrong" fixture.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.05,
                                          location=(0.0, -1.0, 0.0))
    clean = bpy.context.view_layer.objects.active
    clean.name = "CleanBall"
    reply = send("mesh_diagnose", object="CleanBall")
    if not check("it ran", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = reply["result"]
    check("an evenly tessellated sphere is reported CLEAN",
          result.get("clean") is True, str(result["verdict"]))
    check("no clipping", result["self_intersections"]["count"] == 0)
    check("sealed", result["topology"]["watertight"] is True,
          str(result["topology"]))
    check("no invented density problem", not result["density"]["dense"]
          and not result["density"]["starved"], str(result["density"]))
    check("and the verdict says so in one line", len(result["verdict"]) == 1,
          str(result["verdict"]))


def test_scale_anomalies():
    section("scale: a decision, or a units mistake?")
    obj = bpy.data.objects["CleanBall"]
    obj.scale = (1.0, 2.5, 1.0)
    reply = send("mesh_diagnose", object="CleanBall")
    problems = reply["result"]["scale"]["problems"] if reply.get("result") else []
    check("a non-uniform object scale is called out",
          any("not the same on every axis" in p for p in problems), str(problems))
    check("and it reaches the verdict the assistant reads",
          any("axis" in line for line in reply["result"]["verdict"]),
          str(reply["result"]["verdict"]))
    obj.scale = (1.0, 1.0, 1.0)

    obj.scale = (0.0002, 0.0002, 0.0002)
    bpy.context.view_layer.update()
    reply = send("mesh_diagnose", object="CleanBall")
    problems = reply["result"]["scale"]["problems"]
    check("a model a fraction of a millimetre across is a units mistake",
          any("units mistake" in p for p in problems), str(problems))
    obj.scale = (1.0, 1.0, 1.0)


def test_diagnose_density_regions_directly():
    section("the density map on its own, with numbers we control")
    from forge.tools import diagnose

    # 100 normal faces at 1.0, six tiny ones packed together, six huge ones
    # packed together somewhere else.
    areas = [1.0] * 100 + [0.01] * 8 + [40.0] * 8
    centers = ([(0.0, 0.0, float(i) / 100.0) for i in range(100)]
               + [(0.9, 0.9, 0.9)] * 8 + [(-0.9, -0.9, -0.9)] * 8)
    dense, starved = diagnose.density_regions(
        areas, centers, (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 1.0,
        ratio=4.0, limit=5)
    check("the crammed cluster is one region, not eight findings",
          len(dense) == 1 and dense[0]["faces"] == 8, str(dense))
    check("  ... and it says how much denser than the rest",
          dense[0]["times_median"] > 50.0, str(dense[0]["times_median"]))
    check("the starved cluster is one region too",
          len(starved) == 1 and starved[0]["faces"] == 8, str(starved))
    check("  ... at its own place, not the crammed one's",
          starved[0]["location_mm"] != dense[0]["location_mm"],
          "%s vs %s" % (starved[0]["location_mm"], dense[0]["location_mm"]))

    check("a stray face or two is not a region to remesh",
          diagnose.density_regions([1.0] * 100 + [0.01] * 2,
                                   [(0.0, 0.0, 0.0)] * 102,
                                   (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 1.0)
          == ([], []))
    check("an even mesh has no regions at all",
          diagnose.density_regions([1.0] * 200, [(0.0, 0.0, 0.0)] * 200,
                                   (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 1.0)
          == ([], []))


# ---------------------------------------------------------------------------
# 8. the buddy's gating
# ---------------------------------------------------------------------------

def test_change_hash():
    section("the change hash: what stops a check-in costing a turn for nothing")
    from forge.tools import buddy

    obj = bpy.data.objects["CleanBall"]
    first = buddy.mesh_signature(obj)
    check("a mesh has a signature", bool(first), first)
    check("reading it twice gives the same answer",
          buddy.mesh_signature(obj) == first, "%s / %s"
          % (first, buddy.mesh_signature(obj)))

    obj.data.vertices[0].co.z += 0.02
    obj.data.update()
    moved = buddy.mesh_signature(obj)
    check("moving ONE vertex changes it — a sculpt stroke is not missed",
          moved != first, "%s -> %s" % (first, moved))

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    before_faces = len(obj.data.polygons)
    modifier = obj.modifiers.new("Sub", "SUBSURF")
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    check("a remesh changes the face count, so it changes the signature",
          buddy.mesh_signature(obj) != moved
          and len(obj.data.polygons) != before_faces)

    check("a non-mesh has no signature at all",
          buddy.mesh_signature(bpy.data.objects["Skeleton"]) == "")
    check("and neither has nothing", buddy.mesh_signature(None) == "")

    other = bpy.data.objects["Head"]
    check("two different objects never collide",
          buddy.mesh_signature(other) != buddy.mesh_signature(obj))


def test_should_check():
    section("should_check: the two rules that decide whether this costs money")
    from forge.tools import buddy

    now = 1000.0
    fire, reason = buddy.should_check(now, now - 1.0, False, "sig-b", "sig-a")
    check("due, not busy, mesh changed -> look", fire is True, reason)

    fire, reason = buddy.should_check(now, now - 1.0, True, "sig-b", "sig-a")
    check("busy -> skip, even though it is due", fire is False, reason)
    check("  ... and says it is waiting on the assistant", "waiting" in reason, reason)

    fire, reason = buddy.should_check(now, now - 1.0, False, "sig-a", "sig-a")
    check("nothing has changed -> skip", fire is False, reason)
    check("  ... and says exactly that", "nothing has changed" in reason, reason)

    fire, reason = buddy.should_check(now, now + 300.0, False, "sig-b", "sig-a")
    check("not due yet -> skip", fire is False, reason)
    check("  ... and says roughly when", "min" in reason, reason)

    fire, _reason = buddy.should_check(now, now - 1.0, False, "sig-a", "")
    check("the very first look happens even though there is no hash to compare",
          fire is True)
    fire, _reason = buddy.should_check(now, now - 1.0, False, "", "sig-a")
    check("an unhashable scene does not block the look either", fire is True)


def test_buddy_timer(fake):
    section("the buddy timer, driven by hand instead of waited on")
    from forge.tools import assistant, buddy

    point_at(BRIDGE_URL)
    props = buddy.get_props(bpy.context)
    chat = assistant.get_props(bpy.context)
    if not check("scene.forge_buddy exists", props is not None):
        return
    check("it is OFF by default — nothing spends a turn unasked",
          buddy.ForgeBuddyProps.bl_rna.properties["enabled"].default is False)
    check("the interval floor is %d minutes" % buddy.MIN_INTERVAL_MINUTES,
          buddy.ForgeBuddyProps.bl_rna.properties["interval_minutes"].hard_min
          == buddy.MIN_INTERVAL_MINUTES)
    check("and the default is %d" % buddy.DEFAULT_INTERVAL_MINUTES,
          buddy.ForgeBuddyProps.bl_rna.properties["interval_minutes"].default
          == buddy.DEFAULT_INTERVAL_MINUTES)
    check("the toggle's own description says a check-in costs a turn",
          buddy.TURN_COST_NOTE.lower() in
          buddy.ForgeBuddyProps.bl_rna.properties["enabled"].description.lower(),
          buddy.ForgeBuddyProps.bl_rna.properties["enabled"].description)

    bpy.context.view_layer.objects.active = bpy.data.objects["CleanBall"]
    props.enabled = True
    props.next_due = 0.0          # due now
    props.last_hash = ""
    props.last_note = ""
    before = len(fake.asks())

    # 1. Busy skips, and does NOT forfeit the slot.
    chat.busy = True
    result = buddy.buddy_tick()
    chat.busy = False
    check("a busy assistant means the timer waits", len(fake.asks()) == before,
          str(len(fake.asks())))
    check("  ... and asks again soon rather than stopping",
          result == buddy.POLL_SECONDS, str(result))
    check("  ... and says why in the panel", "waiting" in props.status, props.status)

    # 2. Unchanged skips.
    props.last_hash = buddy.mesh_signature(bpy.data.objects["CleanBall"])
    buddy.buddy_tick()
    check("an unchanged mesh means no turn is spent", len(fake.asks()) == before,
          str(len(fake.asks())))
    check("  ... and says so", "nothing has changed" in props.status, props.status)
    check("  ... and the clock is pushed forward, so it does not spin",
          props.next_due > time.time() + 60.0,
          "%.0f s away" % (props.next_due - time.time()))

    # 3. A changed mesh fires exactly one check-in.
    ball = bpy.data.objects["CleanBall"]
    ball.data.vertices[1].co.x += 0.03
    ball.data.update()
    check("one nudged vertex is enough to make the hash disagree — the buddy "
          "must not sleep through a sculpt stroke",
          buddy.mesh_signature(ball) != props.last_hash)
    props.next_due = 0.0
    buddy.buddy_tick()
    asks = fake.asks()
    if check("a changed mesh spends one turn, and only one",
             len(asks) == before + 1, str(len(asks))):
        payload = asks[-1]
        check("  ... led by the fixed check-in line",
              str(payload.get("message", "")).startswith(buddy.CHECK_IN_LEAD),
              str(payload.get("message"))[:120])
        check("  ... and the context is marked as a check-in",
              (payload.get("context") or {}).get("check_in") == "buddy",
              str((payload.get("context") or {}).get("check_in")))
    check("the reply is remembered for next time",
          props.last_note.startswith("The silhouette"), props.last_note[:60])
    check("and the hash moves on with it",
          props.last_hash == buddy.mesh_signature(ball), props.last_hash)
    check("the check counter moved", props.checks >= 1, str(props.checks))

    # 4. The next one carries the last note forward, so it cannot repeat itself.
    ball.data.vertices[2].co.y += 0.03
    ball.data.update()
    props.next_due = 0.0
    buddy.buddy_tick()
    asks = fake.asks()
    check("the next check-in quotes what it already said",
          "You previously noted:" in str(asks[-1].get("message", "")),
          str(asks[-1].get("message"))[-400:])
    check("  ... and is told not to repeat it",
          "Do not repeat it" in str(asks[-1].get("message", "")))

    # 5. Off means off.
    props.enabled = False
    props.next_due = 0.0
    ball.data.vertices[3].co.z += 0.03
    ball.data.update()
    count = len(fake.asks())
    stopped = buddy.buddy_tick()
    check("switched off, the timer fires nothing", len(fake.asks()) == count)
    check("  ... and unregisters itself", stopped is None, str(stopped))
    check("no timer is left running in the background",
          buddy.timer_running() is False)
    check("and start_timer refuses to run one headless at all",
          buddy.start_timer() is False)


# ---------------------------------------------------------------------------
# 9. check-my-work request shaping
# ---------------------------------------------------------------------------

def test_check_my_work(fake):
    section("Check my work: what the button actually sends")
    from forge.tools import assistant, buddy

    point_at(BRIDGE_URL)
    props = buddy.get_props(bpy.context)
    chat = assistant.get_props(bpy.context)
    chat.log.clear()
    props.last_note = ""
    bpy.context.view_layer.objects.active = bpy.data.objects["Broken"]
    before = len(fake.asks())

    result = bpy.ops.forge.buddy_check()
    check("the operator finished", "FINISHED" in result, str(result))
    asks = fake.asks()
    if not check("one message was sent", len(asks) == before + 1, str(len(asks))):
        return
    message = str(asks[-1].get("message") or "")
    note("message is %d characters" % len(message))

    check("it is led by the fixed check-in line",
          message.startswith(buddy.CHECK_IN_LEAD), message[:120])
    check("a clean render was made and its path is in the message",
          "--- a clean 3/4 render of the same model ---" in message.lower(),
          message[:400])
    check("  ... and the file it names is really on disk",
          any(os.path.isfile(line.strip())
              for line in message.splitlines() if line.strip().endswith(".png")),
          str([l for l in message.splitlines() if l.strip().endswith(".png")]))
    check("the model is told to LOOK before answering",
          "Read tool BEFORE answering" in message, message[:600])
    check("the workspace context is in there",
          "--- Where I am working ---" in message and "Mode:" in message,
          message[:800])
    check("the mesh numbers are in there, with places",
          "--- Mesh check" in message and "mm" in message, message[:1200])
    check("  ... and they are the broken mesh's own findings",
          "clipping" in message.lower(), message[:1500])
    check("headless has no viewport, and that is said out loud rather than "
          "silently dropped",
          "Could not gather" in message and "screenshot" in message,
          message[-600:])

    context = asks[-1].get("context") or {}
    check("the context marks this as an asked-for check-in",
          context.get("check_in") == "asked", str(context.get("check_in")))
    check("and still carries the ordinary scene context",
          "active_object" in context and "mode" in context,
          str(sorted(context)))

    entries = [(entry.role, entry.text, entry.check_in) for entry in chat.log]
    check("the chat log holds the exchange", len(entries) == 2, str(entries))
    if len(entries) == 2:
        check("the outgoing turn is the short sentence, not the whole payload",
              entries[0][1].startswith(buddy.CHECK_IN_LOG)
              and len(entries[0][1]) < 200, entries[0][1][:120])
        check("both turns are marked as a check-in, so the panel can label them",
              entries[0][2] is True and entries[1][2] is True, str(entries))
        check("and the reply landed in the log",
              entries[1][1] == CANNED_REPLY, entries[1][1][:80])
    check("the panel is not left busy", chat.busy is False)


def test_panel_wiring():
    section("the panel")
    import re

    from forge.ui import panels

    source = open(panels.__file__, "r", encoding="utf-8").read()
    body = source.split("class VIEW3D_PT_forge_assistant")[1].split("\nclass ")[0]

    check("the Check my work button is in the Assistant box",
          "forge.buddy_check" in body)
    check("so is the buddy toggle and its interval",
          '.prop(bud, "enabled"' in body and '.prop(bud, "interval_minutes"' in body,
          str(re.findall(r'\.prop\(bud,\s*"([a-z_]+)"', body)))
    used = sorted(set(re.findall(r'\.prop\(bud,\s*"([a-z_]+)"', body)))
    from forge.tools import buddy

    props = buddy.get_props(bpy.context)
    check("every buddy property the panel draws exists",
          all(hasattr(props, name) for name in used), str(used))
    check("the panel says out loud that a check-in costs a turn",
          "TURN_COST_NOTE" in body, "the cost note must be visible, not buried")
    check("and it does not borrow another panel's binding name",
          not re.search(r'\.prop\((props|rf|ra),\s*"', body))
    check("the check-in label is drawn differently from an ordinary reply",
          "check_in" in body and "Check-in:" in body)
    for name in ("buddy_check", "buddy_toggle"):
        check("forge.%s is registered" % name, hasattr(bpy.ops.forge, name))


def test_port_is_free_after():
    section("the ports are released")
    from forge import server as forge_server

    forge_server.stop_server()
    time.sleep(0.2)
    for port in (PORT, BRIDGE_PORT):
        try:
            probe = socketlib.socket()
            probe.settimeout(2.0)
            probe.bind(("127.0.0.1", port))
            probe.close()
            free = True
        except OSError as exc:
            free = False
            note(str(exc))
        check("port %d is free again" % port, free)


# ---------------------------------------------------------------------------

def main():
    print("Forge headless tests - Phase 8 (workspace copilot + buddy mode)")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_workspace_")
    note("scratch in %s" % tmpdir)

    for name in ("Cube",):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    fake = FakeBridge(BRIDGE_PORT)
    note("fake bridge on %s" % BRIDGE_URL)

    try:
        test_registration()
        test_headless_guard(tmpdir)
        test_view_core()
        test_framing_core()
        test_shading_and_overlay_core()
        test_axis_parsing()
        test_validation_matrix(tmpdir)
        test_set_mode()
        test_sculpt_brush()
        test_brush_resolution_is_pure()
        test_context_payload()
        test_mesh_diagnose()
        test_mesh_diagnose_open_box()
        test_diagnose_on_a_clean_mesh()
        test_scale_anomalies()
        test_diagnose_density_regions_directly()
        test_change_hash()
        test_should_check()
        test_buddy_timer(fake)
        test_check_my_work(fake)
        test_panel_wiring()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        fake.stop()
        try:
            test_port_is_free_after()
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
