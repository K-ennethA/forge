"""Tests for the Forge web UI — the bridge's second surface (Phase 9).

Run them the same way as the rest::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

The harness is ``test_bridge``'s: a real bridge process on an ephemeral port
with ``fake_claude.py`` standing in for the CLI.  Nothing here touches port
8901, and nothing here needs Blender, PowerShell or the internet — the Blender
socket, the two downstream services and the start script all have fakes in this
file, each on a port the OS handed out.

What is being pinned down, in order:

* the page and its assets are served, and **only** they are — traversal out of
  ``assistant/webui/`` is refused by the shape of the name;
* ``/jobs`` carries enough for a page that has just been opened to draw a
  conversation it never saw happen;
* tokens: minted for an attachment on the way in and for a render path on the
  way out, and ``/file`` serves nothing that was not minted;
* ``/upload``: what a browser can hand this bridge, and what it may not;
* ``/services/health`` fans out and survives everything being down;
* ``/flows`` passes through to Blender's socket, including when it is not there;
* ``/library`` draws every project from a folder read alone, and its thumbnail
  cache photographs the scene without ever building anything to photograph.
"""

import base64
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ASSISTANT_DIR, os.pardir))
WEBUI_DIR = os.path.join(ASSISTANT_DIR, "webui")

for _path in (TESTS_DIR, ASSISTANT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bridge  # noqa: E402
from test_bridge import FAKE_CLI, free_port, start_bridge  # noqa: E402


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

#: A real (tiny) PNG header, so the magic-byte check has something to accept.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 96
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 96


def write_png(path, data=PNG):
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


def raw_get(client, path, timeout=20.0):
    """``(status, headers, body bytes)`` — for assets and files, not JSON."""
    request = urllib.request.Request(client.url(path))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.fixture
def bridges(tmp_path):
    """Start bridges with the web-UI environment pointed at the sandbox."""
    started = []

    def factory(env_extra=None, **kwargs):
        env = {
            # Never write uploads into the repo while testing.
            "FORGE_ASSISTANT_UPLOADS": str(tmp_path / "uploads"),
            # …nor previews, and never read the repo's real projects folder:
            # a test that passes because the machine happens to have four bowl
            # holders in it is not a test.
            "FORGE_ASSISTANT_PREVIEWS": str(tmp_path / "previews"),
            "FORGE_PROJECTS_DIR": str(tmp_path / "projects"),
            # …nor thumbnails: the library's cache is a real folder in the repo
            # by default, and a test must never leave a picture in it.
            "FORGE_ASSISTANT_THUMBS": str(tmp_path / "thumbs"),
            # Ports nothing is listening on, so "down" is the default answer.
            "FORGE_SERVICE_URL": "http://127.0.0.1:%d" % free_port(),
            "FORGE_MESHGEN_URL": "http://127.0.0.1:%d" % free_port(),
            "FORGE_BLENDER_PORT": str(free_port()),
        }
        env.update(env_extra or {})
        proc, client = start_bridge(tmp_path, env_extra=env, **kwargs)
        started.append(proc)
        return client

    yield factory
    for proc in started:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


@pytest.fixture
def client(bridges):
    return bridges()


class FakeService(object):
    """A downstream service that answers ``GET /health`` and nothing else."""

    def __init__(self, payload, status=200):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        body = json.dumps(payload).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/") != "/health":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeBlender(object):
    """The add-on's socket, faked: one newline-delimited JSON exchange.

    ``responder(request) -> response dict``; every request seen is recorded so
    a test can assert on what the passthrough actually sent.
    """

    def __init__(self, responder):
        self.seen = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if not line:
                    return
                try:
                    request = json.loads(line.decode("utf-8"))
                except ValueError:
                    request = {"raw": line.decode("utf-8", "replace")}
                outer.seen.append(request)
                reply = responder(request)
                if reply is None:      # "hang up without answering"
                    return
                self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_service():
    made = []

    def factory(payload, status=200):
        service = FakeService(payload, status)
        made.append(service)
        return service

    yield factory
    for service in made:
        service.close()


@pytest.fixture
def fake_blender():
    made = []

    def factory(responder):
        server = FakeBlender(responder)
        made.append(server)
        return server

    yield factory
    for server in made:
        server.close()


def ok_response(result):
    def responder(request):
        return {"id": request.get("id"), "status": "success", "result": result}
    return responder


def by_type(table, default=None):
    """A FakeBlender responder that answers per command ``type``.

    ``table`` maps a command name to a result dict, or to a callable taking the
    whole request — which is how the preview test writes the PNG the add-on
    would have written.
    """
    def responder(request):
        command = request.get("type")
        if command not in table:
            if default is not None:
                return {"id": request.get("id"), "status": "success",
                        "result": default}
            return {"id": request.get("id"), "status": "error",
                    "message": "Unknown command %r." % command}
        found = table[command]
        result = found(request) if callable(found) else found
        if isinstance(result, str):            # a string means "refuse"
            return {"id": request.get("id"), "status": "error", "message": result}
        return {"id": request.get("id"), "status": "success",
                "result": result if isinstance(result, dict) else {}}
    return responder


# ---------------------------------------------------------------------------
# the workbench's fakes (Phase 11)
# ---------------------------------------------------------------------------

#: A PARAMS schema shaped exactly as docs/architecture.md's contract describes:
#: value and unit always, min/max/step/description where they help.  The page
#: draws a slider from min/max/step, so a schema without them is also covered.
PARAMS = {
    "wall_mm": {"value": 2.4, "unit": "mm", "min": 1.0, "max": 6.0, "step": 0.1,
                "description": "Wall thickness"},
    "feet_count": {"value": 4, "unit": "count", "min": 3, "max": 8, "step": 1,
                   "description": "Number of feet"},
    "hollow": {"value": True, "unit": "bool", "description": "Hollow it out"},
}

#: Enough of a spec.json to draw a component sheet from: what it is, what it is
#: made of, which pieces are core and which are proposals, what prints beside it.
SPEC = {
    "name": "cup",
    "description": "A cup that holds a thing.",
    "parameters": PARAMS,
    "features": ["a fluted band", "four feet"],
    "components": [
        {"name": "cup_core", "kind": "core", "description": "the dimensioned ring"},
        {"name": "cup_collar", "kind": "proposal", "description": "scrap me freely"},
    ],
    "companion_parts": [{"name": "cup-lid", "script": "part_lid.py",
                         "description": "prints separately"}],
    "script": "part.py",
}

PART_SOURCE = (
    "PARAMS = {\n"
    "    \"wall_mm\": {\"value\": 2.4, \"unit\": \"mm\"},\n"
    "}\n\n"
    "def build(p):\n"
    "    return None\n"
)

#: A tetrahedron: four points, four faces, and unmistakable in an assertion.
MESH = {"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]],
        "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]}
STATS = {"vertex_count": 4, "face_count": 4, "bounding_box_mm": [10, 10, 10],
         "watertight": True}


class FakeGeometry(object):
    """The geometry service, faked: ``/health``, ``/parse_params``, ``/generate``.

    Every request body is recorded, because what the bridge *sends* is the
    contract here — an override the page collected has to arrive as the
    service's own ``overrides`` object, in the parameter's declared unit.
    """

    def __init__(self, params=None, mesh=None, stats=None, errors=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.seen = []
        self.params = PARAMS if params is None else params
        self.mesh = MESH if mesh is None else mesh
        self.stats = STATS if stats is None else stats
        self.errors = errors or {}          # endpoint -> (status, payload)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _answer(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/") == "/health":
                    self._answer(200, {"status": "ok", "build123d": "0.9"})
                    return
                self._answer(404, {"error": "no"})

            def do_POST(self):  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    length = 0
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = None
                endpoint = self.path.split("?", 1)[0].rstrip("/") or "/"
                outer.seen.append((endpoint, body))
                if endpoint in outer.errors:
                    status, payload = outer.errors[endpoint]
                    self._answer(status, payload)
                    return
                if endpoint == "/parse_params":
                    self._answer(200, {"params": outer.params})
                    return
                if endpoint == "/generate":
                    self._answer(200, {"params": outer.params,
                                       "mesh": outer.mesh,
                                       "stats": outer.stats})
                    return
                self._answer(404, {"error": "no such endpoint %s" % endpoint})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def bodies(self, endpoint):
        return [body for path, body in self.seen if path == endpoint]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_geometry():
    made = []

    def factory(**kwargs):
        service = FakeGeometry(**kwargs)
        made.append(service)
        return service

    yield factory
    for service in made:
        service.close()


@pytest.fixture
def projects(tmp_path):
    """Write project folders into the sandbox the ``bridges`` fixture points at."""
    root = tmp_path / "projects"

    def make(name, spec=SPEC, script="part.py", source=PART_SOURCE, extra=None):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        if script:
            (folder / script).write_text(source, encoding="utf-8")
        if spec is not None:
            payload = dict(spec)
            payload["name"] = name
            if script:
                payload["script"] = script
            payload.update(extra or {})
            (folder / "spec.json").write_text(json.dumps(payload), encoding="utf-8")
        return folder

    root.mkdir(parents=True, exist_ok=True)
    make.root = root
    return make


# ===========================================================================
# pure units — no process at all
# ===========================================================================

def test_webui_asset_serves_the_page():
    found = bridge.webui_asset("index.html")
    assert found is not None
    body, content_type = found
    assert b"<title>Forge</title>" in body
    assert content_type.startswith("text/html")


@pytest.mark.parametrize("name", [
    "../bridge.py",
    "../system_prompt.md",
    "..\\bridge.py",
    "sub/app.js",
    "sub\\app.js",
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "%2e%2e%2fbridge.py",
    ".hidden.js",
    "",
    "   ",
    None,
])
def test_webui_asset_refuses_anything_that_is_not_one_plain_name(name):
    """Traversal is refused by the *shape* of the name, before path arithmetic.

    That is the check that cannot be got past by encoding: whatever a client
    writes, what arrives here either matches one plain segment or it does not.
    """
    assert bridge.webui_asset(name) is None


def test_webui_asset_refuses_a_file_type_a_browser_has_no_use_for(tmp_path):
    # The extension table is the allow-list, not the folder listing.
    assert bridge.webui_asset("README.md") is None
    assert bridge.webui_asset("app.py") is None


def test_find_file_paths_picks_paths_out_of_prose():
    text = ("I rendered it to C:\\forge\\projects\\cup\\render.png and exported "
            "C:/forge/out/cup.glb — the sketch you gave me was ref.jpg.")
    found = bridge.find_file_paths(text)
    assert "C:\\forge\\projects\\cup\\render.png" in found
    assert "C:/forge/out/cup.glb" in found
    # A bare filename is not a path: there is nothing to open.
    assert not any(p.endswith("ref.jpg") for p in found)


def test_find_file_paths_stops_at_markdown_punctuation():
    found = bridge.find_file_paths("see `C:\\out\\a.png` and (C:\\out\\b.png)")
    assert "C:\\out\\a.png" in found
    assert "C:\\out\\b.png" in found


def test_find_file_paths_ignores_types_that_are_not_served():
    assert bridge.find_file_paths("C:\\forge\\part.py and C:\\forge\\notes.txt") == []


def test_tokens_are_stable_and_only_for_real_servable_files(tmp_path):
    store = bridge.FileTokens(limit=3)
    png = write_png(tmp_path / "a.png")
    token = store.mint(png)
    assert token and store.resolve(token) == os.path.abspath(png)
    # Same path, same token: one render named twice is one entry.
    assert store.mint(png) == token
    # A path that does not exist is not minted: a broken image in the
    # conversation reads as a bug in the UI, not as an answer.
    assert store.mint(str(tmp_path / "missing.png")) is None
    # Nor is a type this bridge will not serve.
    script = tmp_path / "part.py"
    script.write_text("x = 1", encoding="utf-8")
    assert store.mint(str(script)) is None
    assert store.mint("") is None
    assert store.resolve("nope") is None


def test_tokens_evict_the_oldest_when_the_table_is_full(tmp_path):
    store = bridge.FileTokens(limit=2)
    first = store.mint(write_png(tmp_path / "1.png"))
    store.mint(write_png(tmp_path / "2.png"))
    store.mint(write_png(tmp_path / "3.png"))
    assert store.count() == 2
    assert store.resolve(first) is None


def test_upload_error_checks_the_extension_and_the_bytes():
    assert bridge.upload_error("ref.png", PNG) == ""
    assert bridge.upload_error("ref.jpg", JPEG) == ""
    assert "Only images" in bridge.upload_error("part.py", PNG)
    assert "empty" in bridge.upload_error("ref.png", b"")
    # An extension is a claim; this writes to the artist's disk.
    problem = bridge.upload_error("ref.png", b"MZ\x90\x00 not a png")
    assert "not a" in problem and "png" in problem


def test_upload_error_refuses_something_larger_than_the_cap(monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_MAX_UPLOAD_MB", "0.01")
    problem = bridge.upload_error("ref.png", PNG + b"\x00" * 20000)
    assert "limit" in problem


def test_safe_upload_name_is_only_ever_a_filename():
    for hostile in ("../../etc/passwd.png", "C:\\Windows\\evil.png",
                    "..\\..\\bridge.png", "a/b/c.png"):
        name = bridge.safe_upload_name(hostile)
        assert os.path.basename(name) == name
        assert ".." not in name
        assert name.endswith(".png")
    # Two files called the same thing are two files.
    assert bridge.safe_upload_name("ref.png") != bridge.safe_upload_name("ref.png")


def test_decode_base64_takes_a_data_url_or_bare_base64():
    encoded = base64.b64encode(PNG).decode("ascii")
    assert bridge.decode_base64(encoded) == PNG
    assert bridge.decode_base64("data:image/png;base64," + encoded) == PNG
    assert bridge.decode_base64("") is None
    assert bridge.decode_base64("!!!! not base64 !!!!") is None


def test_parse_multipart_finds_the_file_part():
    boundary = "----forgetest"
    body = (
        "--%s\r\nContent-Disposition: form-data; name=\"other\"\r\n\r\nignored\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; "
        "filename=\"sketch.png\"\r\nContent-Type: image/png\r\n\r\n"
        % (boundary, boundary)
    ).encode("utf-8") + PNG + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    name, data = bridge.parse_multipart(body, "multipart/form-data; boundary=" + boundary)
    assert name == "sketch.png"
    assert data == PNG


def test_parse_multipart_without_a_boundary_is_not_an_exception():
    assert bridge.parse_multipart(b"whatever", "multipart/form-data") == (None, None)


def test_start_services_command_runs_a_py_script_under_this_interpreter(tmp_path):
    script = tmp_path / "fake_start.py"
    script.write_text("print('hi')", encoding="utf-8")
    argv = bridge.start_services_command(str(script))
    assert argv[0] == sys.executable and argv[1] == str(script)


def test_start_services_command_uses_powershell_for_a_ps1():
    argv = bridge.start_services_command("C:\\forge\\start_forge.ps1")
    if argv is None:
        pytest.skip("no PowerShell on this machine")
    assert "-File" in argv and argv[-1].endswith("start_forge.ps1")
    assert "-NoProfile" in argv


# ===========================================================================
# the page and its assets
# ===========================================================================

def test_index_is_served_at_the_root(client):
    status, headers, body = raw_get(client, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    text = body.decode("utf-8")
    assert "<title>Forge</title>" in text
    # It has to name its own assets, or the page is a blank screen.
    assert "/webui/app.css" in text and "/webui/app.js" in text
    assert "/webui/format.js" in text
    assert "/webui/follow.js" in text


@pytest.mark.parametrize("asset,content_type,needle", [
    ("app.css", "text/css", "--accent"),
    ("app.js", "text/javascript", "/upload"),
    ("format.js", "text/javascript", "formatReply"),
    ("follow.js", "text/javascript", "projectFromJob"),
    ("index.html", "text/html", "<title>Forge</title>"),
])
def test_assets_are_served_with_their_type(client, asset, content_type, needle):
    status, headers, body = raw_get(client, "/webui/" + asset)
    assert status == 200
    assert headers["Content-Type"].startswith(content_type)
    assert needle in body.decode("utf-8")


@pytest.mark.parametrize("path", [
    "/webui/../bridge.py",
    "/webui/../system_prompt.md",
    "/webui/..%2Fbridge.py",
    "/webui/%2e%2e/bridge.py",
    "/webui/....//bridge.py",
    "/webui/",
    "/webui/nope.js",
    "/webui/README.md",
])
def test_asset_traversal_and_unknown_assets_are_refused(client, path):
    status, _headers, body = raw_get(client, path)
    assert status == 404
    # Whatever the answer was, it was not a file from outside the folder.
    assert b"ForgeAssistant" not in body
    assert b"claude" not in body.lower()


def test_the_page_never_leaks_the_system_prompt(client):
    for path in ("/webui/system_prompt.md", "/webui/..%5Csystem_prompt.md"):
        status, _headers, _body = raw_get(client, path)
        assert status == 404


# ===========================================================================
# the page's structure — fetched from a real bridge, not read off disk
# ===========================================================================

def fetch_text(client, path):
    status, _headers, body = raw_get(client, path)
    assert status == 200, path
    return body.decode("utf-8")


#: Every control the page's contract names, by the id the script reaches for.
#:
#: Phase 14 merged the Chat and Workbench tabs into ONE screen, so the chat's
#: anchors and the part sheet's anchors are now both inside ``panel-studio``.
#: The ids themselves did not move house: the sheet was relocated, not rewritten.
PAGE_ANCHORS = (
    ("thread", "the conversation"),
    ("empty", "the empty state"),
    ("composer", "the send form"),
    ("message", "the message box"),
    ("send", "the send button"),
    ("model", "the Fast/Smart/Deepest selector"),
    ("file", "the image picker"),
    ("attachment", "the attached-image chip"),
    ("attachment-thumb", "the attachment's thumbnail"),
    ("attachment-clear", "the attachment's remove button"),
    ("composer-status", "where 'queued' is said"),
    ("health", "the health strip"),
    ("cost", "the session cost"),
    ("start-services", "Start services"),
    ("new-conversation", "New conversation"),
    ("banners", "where errors land"),
    ("tab-studio", "the studio tab"),
    ("tab-flows", "the flows tab"),
    ("panel-studio", "the studio panel"),
    ("panel-flows", "the flows panel"),
    ("flows", "the flows list"),
    ("flows-refresh", "the flows refresh button"),
    # -- the studio's two columns (Phase 14)
    ("studio-chat", "the conversation column"),
    ("studio-rail", "the part rail"),
    ("follow-note", "whether the rail is following or pinned"),
    ("wb-timing", "how long the last rebuild took"),
    # -- the part sheet (Phase 11, now inside the studio)
    ("flow-buttons", "the always-visible flow row"),
    ("flow-print", "Get ready to print"),
    ("flow-godot", "Send to Godot"),
    ("flow-check", "Check my work"),
    ("saved-flows", "where saved flows are added as buttons"),
    ("project", "the project picker"),
    ("projects-refresh", "the projects refresh button"),
    ("wb-sheet", "the component sheet"),
    ("wb-params", "the parameter controls"),
    ("wb-apply", "Apply & rebuild"),
    ("wb-reset", "Reset values"),
    ("wb-status", "where the workbench says what happened"),
    ("wb-preview", "the inline preview"),
    ("wb-preview-refresh", "the render button"),
    ("wb-view", "which way to look at it"),
    ("scene", "the scene components panel"),
    ("scene-refresh", "the scene refresh button"),
    # -- the library (Phase 13)
    ("tab-library", "the library tab"),
    ("panel-library", "the library panel"),
    ("library", "the card grid"),
    ("library-scene", "the works-in-progress row"),
    ("library-refresh", "the library refresh button"),
)


@pytest.mark.parametrize("anchor,what", PAGE_ANCHORS)
def test_the_page_carries_every_anchor_the_contract_names(client, anchor, what):
    html = fetch_text(client, "/")
    assert ('id="%s"' % anchor) in html, "%s (#%s) is missing" % (what, anchor)


def test_the_script_never_reaches_for_an_element_the_page_lacks(client):
    """The one bug a page with no build step and no framework actually gets.

    ``$("typo")`` is ``null`` and the next line throws, taking the rest of
    ``init()`` with it — a blank screen with a message only in the console.
    So every id the script asks for is checked against the page it was served
    with, both fetched from a running bridge.
    """
    html = fetch_text(client, "/")
    script = fetch_text(client, "/webui/app.js")
    wanted = sorted(set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', script)))
    assert wanted, "no element lookups found — did app.js stop using $()?"
    missing = [name for name in wanted if ('id="%s"' % name) not in html]
    assert not missing, "app.js reaches for ids the page does not have: %s" % missing


def test_the_model_selector_offers_the_three_the_bridge_accepts(client):
    html = fetch_text(client, "/")
    for model in bridge.MODELS:
        assert ('value="%s"' % model) in html, model
    for label in ("Fast", "Smart", "Deepest"):
        assert ">%s<" % label in html, label


def test_the_page_asks_for_nothing_off_this_machine(client):
    """No CDN, no font host, no analytics: this has to work with the wire out.

    Checked on the bytes the bridge actually serves, because "it is offline"
    is the kind of property that is true until somebody adds one convenient
    ``<link>``.
    """
    for path in ("/", "/webui/app.js", "/webui/app.css", "/webui/format.js",
                 "/webui/follow.js"):
        text = fetch_text(client, path)
        for attribute in ("src=", "href=", "@import", "url("):
            for match in re.finditer(re.escape(attribute) + r'\s*["\']?([^"\'\s)>]+)',
                                     text):
                target = match.group(1)
                if target.startswith(("http://", "https://", "//")):
                    # The one allowed absolute URL is the SVG namespace in the
                    # inline favicon: an identifier, never fetched.
                    assert "www.w3.org" in target, "%s fetches %s" % (path, target)


def test_the_page_only_talks_to_routes_this_bridge_serves(client):
    """Every path the script fetches is one of ours, and all of them exist."""
    script = fetch_text(client, "/webui/app.js")
    called = set(re.findall(r'api\("(/[a-z/]*)', script))
    assert called == {"/services/health", "/jobs", "/job/", "/ask", "/cancel/",
                      "/upload", "/new", "/services/start", "/flows",
                      "/flows/run",
                      # the workbench (Phase 11)
                      "/projects", "/projects/", "/preview", "/scene",
                      "/scene/delete",
                      # the library (Phase 13)
                      "/library",
                      # the library's models row: import into the running
                      # Blender, file into a project, open the file
                      "/models/import", "/models/file", "/models/open"}, called


def test_the_reply_is_the_only_html_the_page_ever_builds(client):
    """Anything a model writes is escaped before it is marked up.

    ``innerHTML`` is the one way a reply could put a tag on this page, so there
    is exactly one assignment of it and it comes from the formatter — which
    escapes first and marks up afterwards.  Everything else is textContent.
    """
    script = fetch_text(client, "/webui/app.js")
    assignments = re.findall(r"\.innerHTML\s*=\s*([^;]+);", script)
    assert assignments == ["fmt.formatReply(job.reply)"], assignments


# ===========================================================================
# the formatter — run for real, under node when there is one
# ===========================================================================

FORMAT_HARNESS = """
const fs = require('fs');
globalThis.window = globalThis;
eval(fs.readFileSync(process.argv[2], 'utf8'));
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
process.stdout.write(JSON.stringify(cases.map(window.ForgeFormat.formatReply)));
"""


def format_replies(tmp_path, texts):
    """Run ``format.js`` over some replies and hand back the HTML it made."""
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("no node on this machine to run format.js with")
    harness = tmp_path / "harness.js"
    harness.write_text(FORMAT_HARNESS, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(list(texts)), encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "format.js"), str(cases)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


def test_the_formatter_makes_paragraphs_code_and_bold(tmp_path):
    reply = ("Here is the plan.\nIt has two lines.\n\n"
             "**Wall thickness** is 2.4 mm, set by `wall_mm`.\n\n"
             "```python\nPARAMS = {\"wall_mm\": {\"value\": 2.4}}\n```\n\n"
             "- one\n- two\n\n1. first\n2. second\n\n## A heading")
    html, = format_replies(tmp_path, [reply])
    assert "<p>Here is the plan.<br>It has two lines.</p>" in html
    assert "<strong>Wall thickness</strong>" in html
    assert "<code>wall_mm</code>" in html
    assert "<pre><code>PARAMS = " in html
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert '<ol start="1"><li>first</li><li>second</li></ol>' in html
    assert "<h3>A heading</h3>" in html


def test_the_formatter_escapes_before_it_marks_up(tmp_path):
    """A reply is text a model wrote. It never becomes a tag on this page."""
    cases = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "**<b>bold</b>**",
        "`<script>`",
        "```\n<script>alert(1)</script>\n```",
        "- <script>alert(1)</script>",
    ]
    # The only tags that may come out are the ones the formatter itself makes.
    allowed = {"p", "br", "strong", "em", "code", "pre", "ul", "ol", "li",
               "h3", "a"}
    for html in format_replies(tmp_path, cases):
        made = {name.lower() for name in re.findall(r"</?([A-Za-z][A-Za-z0-9]*)",
                                                    html)}
        assert made <= allowed, html
        # …and what the model wrote is still there, as words rather than markup.
        assert "&lt;" in html


def test_the_formatter_leaves_a_code_sample_alone(tmp_path):
    """Asterisks and underscores inside code are code, not emphasis."""
    html, = format_replies(tmp_path, ["```\na = b ** 2 + _c_\n```"])
    assert "<strong>" not in html and "<em>" not in html
    assert "b ** 2 + _c_" in html


def test_the_formatter_is_safe_on_nothing_at_all(tmp_path):
    assert format_replies(tmp_path, ["", "   \n\n  ", None]) == ["", "", ""]


# ===========================================================================
# /jobs — the conversation a freshly opened page has to draw
# ===========================================================================

def test_jobs_is_empty_before_anything_is_asked(client):
    status, body = client.request("/jobs")
    assert status == 200
    assert body["jobs"] == []
    assert body["session_cost_usd"] == 0
    assert body["busy"] is False and body["queued"] is False
    assert body["limit"] == bridge.MAX_JOBS


def test_jobs_carries_what_the_page_needs_to_redraw_a_turn(client):
    client.turn("make me a cup")
    status, body = client.request("/jobs")
    assert status == 200
    assert len(body["jobs"]) == 1
    job = body["jobs"][0]
    # Everything the conversation view draws, in one request.
    for key in ("job_id", "state", "message", "reply", "activity", "files",
                "created_at", "started_at", "duration_ms", "session_cost_usd"):
        assert key in job, key
    assert job["state"] == "done"
    assert job["message"] == "make me a cup"
    assert job["reply"]
    assert job["cost_usd"] == pytest.approx(0.0123)
    assert body["session_cost_usd"] == pytest.approx(0.0123)


def test_jobs_are_in_the_order_they_were_asked(client):
    client.turn("first")
    client.turn("second")
    client.turn("third")
    _status, body = client.request("/jobs")
    assert [job["message"] for job in body["jobs"]] == ["first", "second", "third"]
    stamps = [job["created_at"] for job in body["jobs"]]
    assert stamps == sorted(stamps)


def test_a_queued_message_shows_as_queued_in_the_jobs_list(bridges):
    client = bridges(env_extra={"FAKE_CLAUDE_MODE": "slow", "FAKE_CLAUDE_SLEEP": "6"})
    first_status, first = client.ask("the slow one")
    assert first_status == 200
    second_status, second = client.ask("the one behind it")
    assert second_status == 200 and second["state"] == "queued"

    _status, body = client.request("/jobs")
    states = {job["job_id"]: job["state"] for job in body["jobs"]}
    assert states[first["job_id"]] == "running"
    assert states[second["job_id"]] == "queued"
    assert body["busy"] is True and body["queued"] is True
    client.request("/cancel/%s" % second["job_id"], payload={})
    client.request("/cancel/%s" % first["job_id"], payload={})


def test_the_job_endpoint_gained_the_same_fields(client):
    _status, asked = client.ask("hello")
    job = client.wait(asked["job_id"])
    assert job["message"] == "hello"
    assert job["created_at"] > 0
    assert job["finished_at"] >= job["started_at"]
    assert isinstance(job["files"], list)


# ===========================================================================
# tokens and /file
# ===========================================================================

def test_an_attachment_is_minted_and_served(client, tmp_path):
    sketch = write_png(tmp_path / "sketch.png")
    _status, asked = client.ask("what is this?", context={"image_path": sketch})
    job = client.wait(asked["job_id"])

    attachments = [f for f in job["files"] if f["source"] == "attachment"]
    assert len(attachments) == 1, job["files"]
    entry = attachments[0]
    assert entry["path"] == os.path.abspath(sketch)
    assert entry["kind"] == "image"
    assert entry["url"] == "/file/" + entry["token"]

    status, headers, body = raw_get(client, entry["url"])
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == PNG


def test_a_render_path_in_the_activity_is_minted(bridges, tmp_path):
    render = write_png(tmp_path / "render.png")
    client = bridges(env_extra={
        "FAKE_CLAUDE_MODE": "stream",
        "FAKE_CLAUDE_RENDER_PATH": render,
        "FORGE_ASSISTANT_TEXT_INTERVAL": "0",
    })
    job = client.turn("render it")
    minted = {f["path"]: f for f in job["files"]}
    assert os.path.abspath(render) in minted, job["files"]
    entry = minted[os.path.abspath(render)]
    assert entry["source"] == "activity"

    status, headers, body = raw_get(client, entry["url"])
    assert status == 200 and body == PNG and headers["Content-Type"] == "image/png"


def test_a_path_in_the_reply_is_minted(bridges, tmp_path):
    out = write_png(tmp_path / "cup.png")
    client = bridges(env_extra={
        "FAKE_CLAUDE_REPLY": "Done — I saved the render to %s, have a look." % out,
    })
    job = client.turn("render it")
    assert [f["source"] for f in job["files"]] == ["reply"]
    assert job["files"][0]["path"] == os.path.abspath(out)


def test_a_glb_is_offered_as_a_file_rather_than_an_image(bridges, tmp_path):
    model = tmp_path / "bug.glb"
    model.write_bytes(b"glTF\x02\x00\x00\x00")
    client = bridges(env_extra={"FAKE_CLAUDE_REPLY": "Exported %s." % model})
    job = client.turn("export it")
    assert job["files"][0]["kind"] == "model"
    status, headers, body = raw_get(client, job["files"][0]["url"])
    assert status == 200
    assert headers["Content-Type"] == "model/gltf-binary"
    assert body.startswith(b"glTF")


def test_a_path_the_model_only_mentioned_is_not_minted(bridges, tmp_path):
    missing = tmp_path / "never-written.png"
    client = bridges(env_extra={
        "FAKE_CLAUDE_REPLY": "I would put it at %s if you asked." % missing})
    job = client.turn("what would you call it")
    assert job["files"] == []


def test_the_same_path_twice_is_one_token(bridges, tmp_path):
    render = write_png(tmp_path / "same.png")
    client = bridges(env_extra={
        "FAKE_CLAUDE_MODE": "stream",
        "FAKE_CLAUDE_RENDER_PATH": render,
        "FAKE_CLAUDE_REPLY": "Saved to %s." % render,
        "FORGE_ASSISTANT_TEXT_INTERVAL": "0",
    })
    job = client.turn("render it")
    # It arrived through the tool arguments, the tool result AND the reply.
    matching = [f for f in job["files"] if f["path"] == os.path.abspath(render)]
    assert len(matching) == 1, job["files"]


@pytest.mark.parametrize("token", [
    "deadbeefdeadbeef",
    "..%2F..%2Fbridge.py",
    "C:%5CWindows%5Cwin.ini",
    "",
    "0",
])
def test_file_serves_nothing_that_was_not_minted(client, token):
    status, _headers, body = raw_get(client, "/file/" + token)
    assert status == 404
    assert b"ForgeAssistant/" not in body


def test_a_readable_file_is_still_refused_without_a_token(client, tmp_path):
    """The allow-list is the bridge's own history, not the filesystem's."""
    readable = write_png(tmp_path / "not-mine.png")
    assert os.path.isfile(readable)
    status, _headers, _body = raw_get(client, "/file/" + readable)
    assert status == 404


def test_a_token_whose_file_vanished_is_a_plain_404(client, tmp_path):
    sketch = write_png(tmp_path / "gone.png")
    _status, asked = client.ask("look", context={"image_path": sketch})
    job = client.wait(asked["job_id"])
    url = job["files"][0]["url"]
    assert raw_get(client, url)[0] == 200
    os.remove(sketch)
    status, _headers, _body = raw_get(client, url)
    assert status == 404


# ===========================================================================
# /upload
# ===========================================================================

def upload(client, name, data, as_data_url=False):
    encoded = base64.b64encode(data).decode("ascii")
    if as_data_url:
        encoded = "data:image/png;base64," + encoded
    return client.request("/upload", {"name": name, "data": encoded})


def test_upload_writes_the_bytes_and_hands_back_a_path(client, tmp_path):
    status, body = upload(client, "sketch.png", PNG)
    assert status == 200, body
    path = body["path"]
    assert os.path.isfile(path)
    assert os.path.dirname(path) == str(tmp_path / "uploads")
    with open(path, "rb") as handle:
        assert handle.read() == PNG
    assert body["bytes"] == len(PNG)
    # And it is immediately viewable, which is how the page draws the thumb.
    assert raw_get(client, body["url"])[2] == PNG


def test_upload_accepts_a_data_url(client):
    status, body = upload(client, "sketch.png", PNG, as_data_url=True)
    assert status == 200, body
    assert os.path.isfile(body["path"])


def test_upload_accepts_multipart(client):
    boundary = "----forgetest"
    payload = (
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; "
        "filename=\"drop.png\"\r\nContent-Type: image/png\r\n\r\n" % boundary
    ).encode("utf-8") + PNG + ("\r\n--%s--\r\n" % boundary).encode("utf-8")

    request = urllib.request.Request(
        client.url("/upload"), data=payload,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert os.path.isfile(body["path"])
    assert body["name"].endswith("drop.png")


@pytest.mark.parametrize("name,data,needle", [
    ("notes.txt", b"hello", "Only images"),
    ("part.py", b"import os", "Only images"),
    ("sketch.png", b"MZ\x90\x00 definitely an exe", "not a"),
    ("sketch.png", b"", "empty"),
])
def test_upload_refuses_what_is_not_an_image(client, name, data, needle):
    status, body = upload(client, name, data)
    assert status == 400, body
    assert needle in body["error"]


def test_upload_refuses_bad_base64(client):
    status, body = client.request("/upload", {"name": "a.png", "data": "!!!!"})
    assert status == 400
    assert "base64" in body["error"]


def test_upload_refuses_a_body_that_is_not_an_object(client):
    status, body = client.request("/upload", {"name": "a.png"})
    assert status == 400
    assert "base64" in body["error"]


def test_upload_is_capped(bridges):
    client = bridges(env_extra={"FORGE_ASSISTANT_MAX_UPLOAD_MB": "0.05"})
    big = PNG + b"\x00" * (80 * 1024)
    status, body = upload(client, "huge.png", big)
    assert status in (400, 413), body
    assert "limit" in body["error"] or "larger" in body["error"]
    # …and the small one still goes through, so the cap is a cap not a wall.
    assert upload(client, "small.png", PNG)[0] == 200


def test_the_uploads_folder_ignores_itself(client, tmp_path):
    """Sketches dropped on a page are not source, and the default folder is
    inside the repo — so it ignores itself rather than making every clone edit
    .gitignore."""
    upload(client, "sketch.png", PNG)
    ignore = tmp_path / "uploads" / ".gitignore"
    assert ignore.is_file()
    assert ignore.read_text(encoding="utf-8").strip() == "*"


def test_pruning_never_evicts_the_folders_own_housekeeping(tmp_path):
    directory = tmp_path / "uploads"
    directory.mkdir()
    (directory / ".gitignore").write_text("*\n", encoding="utf-8")
    for index in range(5):
        write_png(directory / ("shot-%d.png" % index))
    assert bridge.prune_uploads(str(directory), keep=2) == 3
    assert (directory / ".gitignore").is_file()
    assert len([p for p in directory.iterdir() if p.suffix == ".png"]) == 2


def test_upload_cannot_write_outside_the_uploads_folder(client, tmp_path):
    status, body = upload(client, "../../../evil.png", PNG)
    assert status == 200, body
    assert os.path.dirname(body["path"]) == str(tmp_path / "uploads")


def test_an_uploaded_path_rides_context_image_path_exactly_like_the_panel(client):
    """The whole point of /upload: browser bytes become the panel's attachment.

    A browser hands JavaScript file *content*, never a path, so there is
    nothing to put in ``context.image_path`` until the bytes are written down.
    Once they are, the same road is taken: a path in the context, a block in
    the prompt, a Read by the model.
    """
    _status, uploaded = upload(client, "ref.png", PNG)
    path = uploaded["path"]

    _status, asked = client.ask("match these proportions",
                                context={"image_path": path})
    job = client.wait(asked["job_id"])
    assert job["state"] == "done", job

    argv = client.wait_for_calls(1)[-1]["argv"]
    prompt = argv[argv.index("-p") + 1]
    assert bridge.IMAGE_DIVIDER in prompt
    assert path in prompt
    assert "Read tool" in prompt
    # And the attachment is on the job, so a reload still shows the picture.
    assert [f["path"] for f in job["files"]] == [path]


# ===========================================================================
# /services/health
# ===========================================================================

def test_services_health_fans_out_and_reports_one_service_down(bridges, fake_service):
    geometry = fake_service({"status": "ok", "build123d": "0.9"})
    client = bridges(env_extra={
        "FORGE_SERVICE_URL": geometry.url,
        "FORGE_MESHGEN_URL": "http://127.0.0.1:%d" % free_port(),
    })
    status, body = client.request("/services/health")
    assert status == 200
    services = {svc["key"]: svc for svc in body["services"]}
    assert set(services) == {"bridge", "geometry", "meshgen", "blender"}

    assert services["geometry"]["ok"] is True
    assert services["geometry"]["data"]["build123d"] == "0.9"
    assert services["meshgen"]["ok"] is False
    assert "not running" in services["meshgen"]["detail"]
    # Meshgen and Blender are allowed to be down on a working machine.
    assert services["meshgen"]["optional"] is True
    assert services["blender"]["optional"] is True
    assert services["geometry"]["optional"] is False
    assert services["bridge"]["ok"] is True
    assert "session_cost_usd" in body and "claude_cli" in body


def test_services_health_answers_even_with_everything_down(client):
    status, body = client.request("/services/health", timeout=30)
    assert status == 200
    down = [svc["key"] for svc in body["services"] if not svc["ok"]]
    assert set(down) == {"geometry", "meshgen", "blender"}


def test_services_health_sees_blender_listening(bridges, fake_blender):
    server = fake_blender(ok_response({"pong": True}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})
    _status, body = client.request("/services/health")
    services = {svc["key"]: svc for svc in body["services"]}
    assert services["blender"]["ok"] is True
    assert services["blender"]["address"].endswith(":%d" % server.port)


def test_services_health_marks_a_service_that_answers_badly(bridges, fake_service):
    sick = fake_service({"status": "error", "detail": "no build123d"})
    client = bridges(env_extra={"FORGE_SERVICE_URL": sick.url})
    _status, body = client.request("/services/health")
    geometry = [s for s in body["services"] if s["key"] == "geometry"][0]
    assert geometry["ok"] is False
    assert geometry["detail"] == "error"


# ===========================================================================
# /services/start
# ===========================================================================

def fake_start_script(tmp_path, code=0, lines=("[ok] Shape service started",)):
    script = tmp_path / ("fake_start_%d.py" % int(time.time() * 1000 % 1e9))
    body = "\n".join(["print(%r)" % line for line in lines])
    script.write_text("import sys\n%s\nsys.exit(%d)\n" % (body, code), encoding="utf-8")
    return str(script)


def test_start_services_shells_the_script_and_reports_what_it_said(bridges, tmp_path):
    script = fake_start_script(tmp_path, 0,
                               ("  [ok] Shape service started on port 8765",
                                "  [ok] Assistant already running on port 8901"))
    client = bridges(env_extra={"FORGE_START_SCRIPT": script})
    status, body = client.request("/services/start", payload={}, timeout=60)
    assert status == 200, body
    assert body["ok"] is True and body["returncode"] == 0
    assert body["script"] == script
    assert any("Shape service started" in line for line in body["output"])
    assert any("already running" in line for line in body["output"])


def test_start_services_reports_a_failing_script_without_pretending(bridges, tmp_path):
    script = fake_start_script(tmp_path, 3, ("  [X] The shape service did not start.",))
    client = bridges(env_extra={"FORGE_START_SCRIPT": script})
    status, body = client.request("/services/start", payload={}, timeout=60)
    assert status == 200
    assert body["ok"] is False and body["returncode"] == 3
    assert any("did not start" in line for line in body["output"])


def test_start_services_says_so_when_the_script_is_missing(bridges, tmp_path):
    client = bridges(env_extra={
        "FORGE_START_SCRIPT": str(tmp_path / "nope" / "start_forge.ps1")})
    status, body = client.request("/services/start", payload={}, timeout=60)
    assert status == 404
    assert "missing" in body["error"]


def test_the_real_start_script_is_still_where_the_route_expects_it():
    """A guard, not a run: this route is worthless pointed at nothing."""
    assert os.path.isfile(os.path.join(REPO_ROOT, "start_forge.ps1"))


# ===========================================================================
# /flows — passthrough to the Blender socket
# ===========================================================================

FLOW_LIST = {
    "dir": "C:\\forge\\flows",
    "count": 1,
    "flows": [{
        "name": "segment-into-4",
        "description": "Cut the part into 4 wedges that fit the bed.",
        "params": {"wedges": {"value": 4, "unit": "count",
                              "description": "How many wedges"}},
        "steps": 2,
        "step_labels": ["Cut it up", "Show the pieces"],
        "path": "C:\\forge\\flows\\segment-into-4.json",
    }],
}


def test_flows_proxies_flow_list(bridges, fake_blender):
    server = fake_blender(ok_response(FLOW_LIST))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/flows", payload={})
    assert status == 200, body
    assert body["count"] == 1
    assert body["flows"][0]["name"] == "segment-into-4"
    assert body["flows"][0]["params"]["wedges"]["value"] == 4
    assert server.seen[0]["type"] == "flow_list"


def test_flows_run_sends_the_name_and_params_it_was_given(bridges, fake_blender):
    report = {"flow": "segment-into-4", "ok": True, "count": 2,
              "duration_ms": 1234,
              "steps": [{"index": 0, "label": "Cut it up", "brief": "segments=6"},
                        {"index": 1, "label": "Show the pieces", "brief": "count=6"}]}
    server = fake_blender(ok_response(report))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})

    status, body = client.request("/flows/run",
                                  payload={"name": "segment-into-4",
                                           "params": {"wedges": 6}})
    assert status == 200, body
    assert body["ok"] is True and body["count"] == 2
    request = server.seen[0]
    assert request["type"] == "flow_run"
    assert request["params"]["name"] == "segment-into-4"
    assert request["params"]["params"] == {"wedges": 6}


def test_flows_run_with_no_params_still_sends_an_object(bridges, fake_blender):
    server = fake_blender(ok_response({"ok": True, "count": 0, "steps": []}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})
    status, _body = client.request("/flows/run", payload={"name": "whatever"})
    assert status == 200
    assert server.seen[0]["params"]["params"] == {}


def test_flows_run_needs_a_name(client):
    status, body = client.request("/flows/run", payload={})
    assert status == 400
    assert "Which flow" in body["error"]


def test_flows_run_refuses_params_that_are_not_an_object(client):
    status, body = client.request("/flows/run",
                                  payload={"name": "x", "params": [1, 2, 3]})
    assert status == 400
    assert "object" in body["error"]


def test_a_flow_that_failed_reports_blenders_own_words(bridges, fake_blender):
    message = ("Flow 'segment-into-4' failed at step 2 of 2 (Show the pieces): "
               "no such object  (done first: 1 Cut it up)")

    def responder(request):
        return {"id": request.get("id"), "status": "error", "message": message}

    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/flows/run", payload={"name": "segment-into-4"})
    assert status == 502
    assert body["error"] == message
    assert body["blender"] is True


def test_flows_says_blender_is_not_running_in_words_an_artist_can_act_on(client):
    for path in ("/flows", "/flows/run"):
        payload = {"name": "x"} if path.endswith("run") else {}
        status, body = client.request(path, payload=payload)
        assert status == 503, (path, body)
        assert body["blender"] is False
        assert "Blender is not running" in body["error"]
        assert "Start Server" in body["error"]


def test_a_blender_that_hangs_up_is_an_error_not_a_hang(bridges, fake_blender):
    server = fake_blender(lambda request: None)   # accept, then close
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/flows", payload={}, timeout=40)
    assert status in (502, 503), body
    assert body["error"]


def test_a_blender_that_answers_with_junk_is_an_error(bridges):
    """A socket that sends a line of non-JSON must not take the bridge down."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _addr = server.accept()
            conn.recv(65536)
            conn.sendall(b"not json at all\n")
            conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(port)})
    try:
        status, body = client.request("/flows", payload={}, timeout=40)
        assert status == 502
        assert "not JSON" in body["error"]
    finally:
        server.close()


# ===========================================================================
# the two surfaces are one conversation
# ===========================================================================

def test_the_web_ui_and_the_panel_share_the_session_and_the_jobs(client):
    """Nothing about /jobs is a separate history: it is *the* history."""
    panel_job = client.turn("asked from the panel")
    _status, jobs = client.request("/jobs")
    assert panel_job["job_id"] in [job["job_id"] for job in jobs["jobs"]]

    # /new is the same reset both surfaces press.
    assert client.request("/new", payload={})[0] == 200
    _status, after = client.request("/jobs")
    assert after["session_cost_usd"] == 0
    # The history stays: forgetting the conversation is not erasing the page.
    assert len(after["jobs"]) == 1


def test_the_health_route_the_panel_uses_is_untouched(client):
    status, body = client.request("/health")
    assert status == 200
    for key in ("status", "claude_cli", "busy", "queued", "session_cost_usd",
                "last_auth_error"):
        assert key in body, key


def test_unknown_paths_are_still_a_clean_404(client):
    for path in ("/nope", "/webui", "/file"):
        status, body = client.request(path)
        assert status == 404, path
        assert "error" in body


# ===========================================================================
# the workbench (Phase 11) — pure units
# ===========================================================================

@pytest.mark.parametrize("script,expected", [
    ("C:\\forge\\projects\\eevee-bowl-holder\\part.py", "eevee-bowl-holder"),
    ("C:\\forge\\projects\\bowl-holder\\part_base_ring.py", "part_base_ring"),
    ("C:/forge/projects/cup/main.py", "cup"),
    ("C:/forge/projects/cup/__init__.py", "cup"),
    ("C:/forge/projects/cup/lid.py", "lid"),
])
def test_object_name_for_script_follows_the_naming_convention(script, expected):
    """The workbench has to name the same object the panel and MCP name.

    Otherwise Apply replaces the mesh of an object nobody is looking at, and
    the artist watches a slider do nothing.  docs/architecture.md's rule: the
    stem, or the folder when the stem is generic.
    """
    assert bridge.object_name_for_script(script) == expected


def test_object_name_is_capped_the_way_blender_caps_it():
    long_name = "x" * 200
    name = bridge.object_name_for_script("C:/p/%s/part.py" % long_name)
    assert len(name.encode("utf-8")) <= 63


@pytest.mark.parametrize("name", [
    "../system_prompt.md", "..\\bridge.py", "sub/thing", "sub\\thing",
    "%2e%2e", "C:\\Windows", "/etc", "", "   ", None, ".", "..",
])
def test_project_dir_refuses_anything_that_is_not_one_plain_name(name, monkeypatch,
                                                                tmp_path):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path))
    assert bridge.project_dir(name) is None


def test_scan_projects_reads_the_spec_and_finds_the_script(monkeypatch, tmp_path,
                                                           projects):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    projects("cup")
    found = bridge.scan_projects()
    assert found["count"] == 1
    entry = found["projects"][0]
    assert entry["name"] == "cup"
    assert entry["script"] == "part.py"
    assert entry["has_params"] is True
    # The object the panel would build into, not "part".
    assert entry["object"] == "cup"
    assert entry["spec"]["features"] == ["a fluted band", "four feet"]


def test_scan_projects_prefers_the_script_the_spec_names(monkeypatch, projects):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    folder = projects("ring", script="part_base_ring.py")
    (folder / "part_collar_band.py").write_text(PART_SOURCE, encoding="utf-8")
    entry = bridge.scan_projects()["projects"][0]
    assert entry["script"] == "part_base_ring.py"
    assert set(entry["scripts"]) == {"part_base_ring.py", "part_collar_band.py"}


def test_scan_projects_skips_folders_that_are_not_projects(monkeypatch, projects):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    projects("cup")
    (projects.root / "notes").mkdir()
    (projects.root / "notes" / "todo.txt").write_text("later", encoding="utf-8")
    assert [p["name"] for p in bridge.scan_projects()["projects"]] == ["cup"]


def test_scan_projects_survives_a_spec_that_will_not_parse(monkeypatch, projects):
    """A trailing comma in the artist's own file must not hide their part."""
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    folder = projects("cup")
    (folder / "spec.json").write_text("{ not json,", encoding="utf-8")
    entry = bridge.scan_projects()["projects"][0]
    assert entry["spec"] is None
    assert entry["script"] == "part.py" and entry["has_params"] is True


def test_scan_projects_says_so_when_there_is_no_folder_at_all(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path / "nope"))
    found = bridge.scan_projects()
    assert found["projects"] == [] and found["count"] == 0
    assert "no projects folder" in found["note"]


def test_script_has_params_reads_the_file_rather_than_importing_it(tmp_path):
    good = tmp_path / "part.py"
    good.write_text(PART_SOURCE, encoding="utf-8")
    assert bridge.script_has_params(str(good)) is True

    # Executing this would end the test run; reading it is a False.
    bad = tmp_path / "hostile.py"
    bad.write_text("import sys\nsys.exit(1)\n# PARAMS is only in a comment\n",
                   encoding="utf-8")
    assert bridge.script_has_params(str(bad)) is False
    assert bridge.script_has_params(str(tmp_path / "missing.py")) is False


def test_prune_previews_keeps_the_newest_and_never_touches_anything_else(tmp_path):
    directory = tmp_path / "previews"
    directory.mkdir()
    keep_me = directory / "notes.txt"
    keep_me.write_text("hello", encoding="utf-8")
    for index in range(6):
        write_png(directory / ("preview-%d.png" % index))
        time.sleep(0.01)
    assert bridge.prune_previews(str(directory), keep=2) == 4
    assert keep_me.is_file()
    assert len([p for p in directory.iterdir() if p.suffix == ".png"]) == 2


def test_new_preview_path_is_a_fresh_name_in_the_bridges_own_folder(monkeypatch,
                                                                   tmp_path):
    monkeypatch.setenv("FORGE_ASSISTANT_PREVIEWS", str(tmp_path / "previews"))
    first = bridge.new_preview_path()
    second = bridge.new_preview_path()
    assert first != second, "a reused name is a cached picture of the old shape"
    assert os.path.dirname(first) == str(tmp_path / "previews")
    assert first.endswith(".png")
    assert os.path.isdir(str(tmp_path / "previews"))


def test_service_post_keeps_not_running_and_refused_apart(monkeypatch,
                                                          fake_geometry):
    """One is a button to press; the other is a message about your script."""
    monkeypatch.setenv("FORGE_SERVICE_URL", "http://127.0.0.1:%d" % free_port())
    with pytest.raises(bridge.ServiceDown) as down:
        bridge.service_post("/parse_params", {"script": "x"}, 5.0)
    assert "Start services" in str(down.value)

    sick = fake_geometry(errors={"/parse_params": (400, {
        "error": "wall_mm must be between 1.0 and 6.0"})})
    monkeypatch.setenv("FORGE_SERVICE_URL", sick.url)
    with pytest.raises(bridge.ServiceRefused) as refused:
        bridge.service_post("/parse_params", {"script": "x"}, 5.0)
    assert str(refused.value) == "wall_mm must be between 1.0 and 6.0"


def test_project_schema_is_cached_until_the_script_changes(monkeypatch, projects,
                                                           fake_geometry):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    service = fake_geometry()
    monkeypatch.setenv("FORGE_SERVICE_URL", service.url)
    bridge.forget_schema()

    script = str(projects("cup") / "part.py")
    params, cached = bridge.project_schema(script)
    assert cached is False and params["wall_mm"]["value"] == 2.4
    assert bridge.project_schema(script)[1] is True
    assert len(service.bodies("/parse_params")) == 1

    # An edit in another window is not something to wait fifteen seconds for.
    with open(script, "a", encoding="utf-8") as handle:
        handle.write("\n# edited\n")
    os.utime(script, (time.time() + 5, time.time() + 5))
    assert bridge.project_schema(script)[1] is False
    assert len(service.bodies("/parse_params")) == 2

    # …and refresh=True skips the cache outright.
    assert bridge.project_schema(script, refresh=True)[1] is False
    assert len(service.bodies("/parse_params")) == 3


# ===========================================================================
# the workbench — GET /projects and GET /projects/<name>/schema
# ===========================================================================

def test_projects_is_empty_before_anything_is_made(client):
    status, body = client.request("/projects")
    assert status == 200
    assert body["projects"] == [] and body["count"] == 0
    assert body["dir"].endswith("projects")


def test_projects_lists_what_is_on_disk(bridges, projects):
    projects("cup")
    projects("lid", script="part_lid.py")
    client = bridges()
    status, body = client.request("/projects")
    assert status == 200, body
    names = [p["name"] for p in body["projects"]]
    assert names == ["cup", "lid"]
    cup = body["projects"][0]
    assert cup["script"] == "part.py"
    assert cup["object"] == "cup"
    assert cup["has_params"] is True
    assert cup["spec"]["description"] == "A cup that holds a thing."
    assert cup["spec"]["components"][0]["kind"] == "core"
    assert cup["spec"]["companion_parts"][0]["script"] == "part_lid.py"


def test_schema_asks_the_service_for_the_resolved_params(bridges, projects,
                                                         fake_geometry):
    projects("cup")
    service = fake_geometry()
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url})
    status, body = client.request("/projects/cup/schema")
    assert status == 200, body
    assert body["project"] == "cup" and body["script"] == "part.py"
    assert body["object"] == "cup"
    assert body["count"] == 3
    assert body["params"]["wall_mm"]["min"] == 1.0
    assert body["params"]["feet_count"]["unit"] == "count"
    # The source went to the service; the bridge never runs the artist's script.
    sent = service.bodies("/parse_params")[0]
    assert "PARAMS" in sent["script"]


@pytest.mark.parametrize("name", ["nope", "..", "..%2Fassistant", "sub%2Fthing"])
def test_schema_of_a_project_that_is_not_one_is_a_plain_404(bridges, projects, name):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/%s/schema" % name)
    assert status == 404, body
    assert "No project called" in body["error"]


def test_schema_with_the_service_down_is_one_sentence_with_a_button_in_it(bridges,
                                                                         projects):
    projects("cup")
    client = bridges()          # the default service URL has nothing on it
    status, body = client.request("/projects/cup/schema", timeout=40)
    assert status == 503, body
    assert body["service"] is False
    assert "shape service is not running" in body["error"]
    assert "Start services" in body["error"]


def test_schema_passes_the_services_own_complaint_through(bridges, projects,
                                                          fake_geometry):
    service = fake_geometry(errors={"/parse_params": (400, {
        "error": "PARAMS['wall_mm'] has no 'unit'."})})
    projects("cup")
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url})
    status, body = client.request("/projects/cup/schema")
    assert status == 502, body
    assert body["service"] is True
    assert body["error"] == "PARAMS['wall_mm'] has no 'unit'."


# ===========================================================================
# the workbench — POST /projects/<name>/set_params
# ===========================================================================

def set_params_blender():
    """A fake add-on that accepts the two commands the chain sends."""
    return by_type({
        "partforge_open": lambda request: {
            "script": (request.get("params") or {}).get("script_path"),
            "param_count": 3, "object": (request.get("params") or {}).get("object"),
            "schema_source": "service"},
        "load_mesh": lambda request: {
            "object": (request.get("params") or {}).get("name"),
            "vertex_count": 4, "face_count": 4},
    })


def test_set_params_generates_then_loads_the_mesh_in_place(bridges, projects,
                                                           fake_geometry,
                                                           fake_blender):
    projects("cup")
    service = fake_geometry()
    blender = fake_blender(set_params_blender())
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url,
                                "FORGE_BLENDER_PORT": str(blender.port)})

    status, body = client.request("/projects/cup/set_params",
                                  payload={"overrides": {"wall_mm": 3.2,
                                                         "feet_count": 6}},
                                  timeout=60)
    assert status == 200, body
    assert body["loaded"] is True and body["blender"] is True
    assert body["object"] == "cup"
    assert body["stats"]["face_count"] == 4
    assert body["overrides"] == {"wall_mm": 3.2, "feet_count": 6}

    # -- what the shape service was asked for
    generated = service.bodies("/generate")
    assert len(generated) == 1
    assert generated[0]["overrides"] == {"wall_mm": 3.2, "feet_count": 6}
    assert "def build" in generated[0]["script"]

    # -- and what Blender was asked to do, in order
    types = [request["type"] for request in blender.seen]
    assert types == ["partforge_open", "load_mesh"], types
    opened = blender.seen[0]["params"]
    assert opened["script_path"].endswith(os.path.join("cup", "part.py"))
    assert opened["object"] == "cup"
    loaded = blender.seen[1]["params"]
    # ``replace`` is the whole point: the object's transform, its place in the
    # outliner and the artist's selection all survive a rebuild.
    assert loaded["replace"] is True
    assert loaded["name"] == "cup"
    assert loaded["vertices"] == MESH["vertices"]
    assert loaded["faces"] == MESH["faces"]


def test_set_params_with_no_overrides_still_rebuilds(bridges, projects,
                                                     fake_geometry, fake_blender):
    projects("cup")
    service = fake_geometry()
    blender = fake_blender(set_params_blender())
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url,
                                "FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/set_params", payload={}, timeout=60)
    assert status == 200, body
    assert service.bodies("/generate")[0]["overrides"] == {}


def test_set_params_refuses_overrides_that_are_not_an_object(bridges, projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/cup/set_params",
                                  payload={"overrides": [1, 2, 3]})
    assert status == 400
    assert "object" in body["error"]


def test_set_params_with_the_service_down_never_reaches_blender(bridges, projects,
                                                                fake_blender):
    projects("cup")
    blender = fake_blender(set_params_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/set_params", payload={},
                                  timeout=40)
    assert status == 503, body
    assert body["service"] is False
    assert "Start services" in body["error"]
    assert blender.seen == [], "the mesh was never built; nothing to load"


def test_set_params_reports_the_services_own_words_about_a_bad_value(
        bridges, projects, fake_geometry):
    service = fake_geometry(errors={"/generate": (400, {
        "error": "wall_mm=99 is outside 1.0..6.0"})})
    projects("cup")
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url})
    status, body = client.request("/projects/cup/set_params",
                                  payload={"overrides": {"wall_mm": 99}})
    assert status == 502, body
    assert body["service"] is True
    assert body["error"] == "wall_mm=99 is outside 1.0..6.0"


def test_set_params_with_blender_closed_still_hands_back_the_numbers(
        bridges, projects, fake_geometry):
    """Built but not shown. Hiding the stats would be a lie of omission."""
    projects("cup")
    service = fake_geometry()
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url})
    status, body = client.request("/projects/cup/set_params",
                                  payload={"overrides": {"wall_mm": 3.0}},
                                  timeout=40)
    assert status == 503, body
    assert body["blender"] is False and body["loaded"] is False
    assert "Blender is not running" in body["error"]
    assert "Start Server" in body["error"]
    assert body["stats"]["face_count"] == 4


def test_set_params_survives_a_panel_that_refuses_to_follow_along(
        bridges, projects, fake_geometry, fake_blender):
    """The panel not tracking is smaller than the mesh not arriving."""
    projects("cup")
    service = fake_geometry()
    blender = fake_blender(by_type({
        "partforge_open": lambda request: "that script is not where you said",
        "load_mesh": lambda request: {
            "object": (request.get("params") or {}).get("name"),
            "vertex_count": 4, "face_count": 4},
    }))
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url,
                                "FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/set_params", payload={},
                                  timeout=60)
    assert status == 200, body
    assert body["loaded"] is True
    assert any("did not follow along" in note for note in body["notes"])


def test_set_params_says_so_when_the_service_builds_nothing(bridges, projects,
                                                            fake_geometry,
                                                            fake_blender):
    service = fake_geometry(mesh={"vertices": [], "faces": []})
    projects("cup")
    blender = fake_blender(set_params_blender())
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url,
                                "FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/set_params", payload={},
                                  timeout=60)
    assert status == 200, body
    assert body["loaded"] is False
    assert any("empty mesh" in note for note in body["notes"])
    assert blender.seen == []


def test_set_params_reports_a_blender_that_refuses_the_mesh(bridges, projects,
                                                            fake_geometry,
                                                            fake_blender):
    projects("cup")
    service = fake_geometry()
    blender = fake_blender(by_type({
        "partforge_open": {},
        "load_mesh": lambda request: "faces reference vertex 9 of 4",
    }))
    client = bridges(env_extra={"FORGE_SERVICE_URL": service.url,
                                "FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/set_params", payload={},
                                  timeout=60)
    assert status == 502, body
    assert body["blender"] is True and body["loaded"] is False
    assert body["error"] == "faces reference vertex 9 of 4"


def test_set_params_of_a_project_that_is_not_one_is_a_404(bridges, projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/..%5Cassistant/set_params", payload={})
    assert status == 404
    assert "No project called" in body["error"]


# ===========================================================================
# the workbench — POST /preview
# ===========================================================================

def preview_blender(written=None):
    def render(request):
        params = request.get("params") or {}
        path = params.get("path")
        if written is not None:
            written.append(path)
        write_png(path)
        return {"path": path, "objects": params.get("objects") or [],
                "view": params.get("view") or "iso", "resolution": 768,
                "framed_all_visible": not params.get("objects"),
                "bounds_mm": {"size": [10, 10, 10]}}
    return by_type({"render_preview": render})


def test_preview_renders_mints_a_token_and_serves_the_png(bridges, fake_blender,
                                                          tmp_path):
    written = []
    blender = fake_blender(preview_blender(written))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})

    status, body = client.request("/preview", payload={}, timeout=60)
    assert status == 200, body
    assert body["token"] and body["url"] == "/file/" + body["token"]
    # The path is the bridge's, never the client's.
    assert os.path.dirname(body["path"]) == str(tmp_path / "previews")
    assert written == [body["path"]]
    assert body["framed_all_visible"] is True

    # …and this is how the page actually shows a component.
    status, headers, png = raw_get(client, body["url"])
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert png == PNG


def test_preview_passes_the_objects_and_the_view_through(bridges, fake_blender):
    blender = fake_blender(preview_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request(
        "/preview", payload={"objects": ["cup_collar"], "view": "front",
                             "resolution": 512, "shading": "material"},
        timeout=60)
    assert status == 200, body
    sent = blender.seen[0]["params"]
    assert sent["objects"] == ["cup_collar"]
    assert sent["view"] == "front" and sent["shading"] == "material"
    assert sent["resolution"] == 512
    assert body["view"] == "front"


def test_preview_never_renders_to_a_path_the_client_named(bridges, fake_blender,
                                                          tmp_path):
    """A route that writes where it is told writes wherever it is told."""
    blender = fake_blender(preview_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    hostile = str(tmp_path / "evil.png")
    status, body = client.request("/preview", payload={"path": hostile},
                                  timeout=60)
    assert status == 200, body
    assert blender.seen[0]["params"]["path"] != hostile
    assert not os.path.exists(hostile)


def test_preview_refuses_objects_that_are_not_names(bridges, fake_blender):
    blender = fake_blender(preview_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/preview", payload={"objects": [{"name": "cup"}]})
    assert status == 400
    assert "object names" in body["error"]


def test_preview_says_so_when_the_render_produced_no_file(bridges, fake_blender):
    blender = fake_blender(by_type({
        "render_preview": lambda request: {
            "path": (request.get("params") or {}).get("path")}}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/preview", payload={}, timeout=60)
    assert status == 502, body
    assert "no readable" in body["error"]


def test_preview_with_blender_closed_names_the_button_to_press(client):
    status, body = client.request("/preview", payload={}, timeout=40)
    assert status == 503, body
    assert body["blender"] is False
    assert "Blender is not running" in body["error"]
    assert "Start Server" in body["error"]


# ===========================================================================
# the workbench — GET/POST /scene and POST /scene/delete
# ===========================================================================

SCENE = {
    "objects": [
        {"name": "cup_core", "type": "MESH", "location": [0, 0, 0],
         "dimensions": [0.152, 0.152, 0.062], "vertex_count": 4212,
         "face_count": 4100, "modifiers": []},
        {"name": "cup_collar", "type": "MESH", "location": [0, 0, 0.06],
         "dimensions": [0.164, 0.164, 0.02], "vertex_count": 900,
         "face_count": 880, "modifiers": []},
    ],
    "active": "cup_core",
}


def test_scene_is_get_scene_info_verbatim(bridges, fake_blender):
    blender = fake_blender(by_type({"get_scene_info": SCENE}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/scene")
    assert status == 200, body
    assert [o["name"] for o in body["objects"]] == ["cup_core", "cup_collar"]
    assert body["active"] == "cup_core"
    assert blender.seen[0]["type"] == "get_scene_info"


def test_scene_answers_a_post_too(bridges, fake_blender):
    """Both verbs, because a page that got it wrong would silently show nothing."""
    blender = fake_blender(by_type({"get_scene_info": SCENE}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/scene", payload={})
    assert status == 200 and len(body["objects"]) == 2


def test_scene_with_blender_closed_is_the_panels_own_sentence(client):
    status, body = client.request("/scene", timeout=40)
    assert status == 503, body
    assert body["blender"] is False
    assert "Blender is not running" in body["error"]
    assert "press N" in body["error"] and "Start Server" in body["error"]


def test_scene_delete_scraps_one_object_and_says_it_is_undoable(bridges,
                                                                fake_blender):
    blender = fake_blender(by_type({"delete_object": {}}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/scene/delete", payload={"object": "cup_collar"})
    assert status == 200, body
    assert body["deleted"] == "cup_collar"
    # The add-on pushes an undo step per state-changing command, so this is a
    # fact rather than a comfort.
    assert body["undo"] == "Ctrl+Z in Blender puts cup_collar back."
    request = blender.seen[0]
    assert request["type"] == "delete_object"
    assert request["params"] == {"name": "cup_collar"}


def test_scene_delete_needs_to_be_told_what_to_scrap(client):
    status, body = client.request("/scene/delete", payload={})
    assert status == 400
    assert "Which object" in body["error"]


def test_scene_delete_reports_blenders_refusal(bridges, fake_blender):
    blender = fake_blender(by_type({
        "delete_object": lambda request: "No object called 'ghost'."}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/scene/delete", payload={"object": "ghost"})
    assert status == 502, body
    assert body["error"] == "No object called 'ghost'."
    assert body["blender"] is True


def test_scene_delete_with_blender_closed_names_the_button(client):
    status, body = client.request("/scene/delete", payload={"object": "cup"},
                                  timeout=40)
    assert status == 503, body
    assert body["blender"] is False


# ===========================================================================
# the flow buttons — the canned messages, verbatim
# ===========================================================================

#: The exact text each button sends. These are the product: an artist should
#: never have to type this paragraph again, and a reworded one is a different
#: instruction to the model.
CANNED = {
    "flow-print": (
        "Get ready to print",
        "Get the current work ready to print: merge what's in the scene if "
        "needed, run the checks, fix what you can, segment if it doesn't fit "
        "the bed, and export. Tell me what you did."),
    "flow-godot": (
        "Send to Godot",
        "Take the current character through retopo/tags/rig if not done and "
        "export it for Godot. Tell me where the files are."),
    "flow-check": (
        "Check my work",
        "[check-in] Look at my work and tell me what you notice."),
}


@pytest.mark.parametrize("anchor", sorted(CANNED))
def test_the_flow_buttons_carry_their_message_verbatim(client, anchor):
    html = fetch_text(client, "/")
    label, message = CANNED[anchor]
    assert ('id="%s"' % anchor) in html
    assert ('data-canned="%s"' % message) in html, message
    assert (">%s<" % label) in html, label


def test_the_check_in_button_says_exactly_what_the_addon_says():
    """One phrase, two surfaces.

    The system prompt keys its "checking their work" stance off this exact
    opening, and the Blender panel's own Check my work button sends it — so a
    reworded button here would be a check-in the model does not recognise.
    """
    path = os.path.join(REPO_ROOT, "addon", "forge", "tools", "buddy.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert ('CHECK_IN_LEAD = "%s"' % CANNED["flow-check"][1]) in source


def test_the_flow_row_is_outside_both_panels(client):
    """Always visible means always visible: above <main>, not inside a tab."""
    html = fetch_text(client, "/")
    row = html.index('id="flow-buttons"')
    assert row < html.index("<main>")
    assert row > html.index('id="tab-studio"')


def test_the_studio_tab_and_its_panel_are_wired_to_each_other(client):
    html = fetch_text(client, "/")
    assert 'aria-controls="panel-studio"' in html
    assert 'aria-labelledby="tab-studio"' in html
    # Three tabs, not four: the Chat and the Workbench are one screen now, and
    # the two that remain are the errands that really are separate.
    script = fetch_text(client, "/webui/app.js")
    assert 'var TABS = ["studio", "library", "flows"];' in script
    assert 'id="tab-chat"' not in html and 'id="tab-workbench"' not in html


# ===========================================================================
# keep-alive: the bug the workbench found
# ===========================================================================

@pytest.mark.parametrize("first", ["/flows", "/scene", "/new", "/services/start"])
def test_a_post_body_never_poisons_the_next_request_on_the_connection(bridges,
                                                                     tmp_path,
                                                                     first):
    """Every POST handler must drain its body, even one that ignores it.

    HTTP/1.1 here is keep-alive.  A body left in the socket is read as the
    start of the *next* request on that connection, which surfaces as a 501
    "Unsupported method ('{}GET')" on some later, innocent route — a bug that
    looks like it belongs to whichever page happened to ask second.  A browser
    reuses connections; urllib does not, so this test speaks http.client.
    """
    import http.client

    client = bridges(env_extra={
        "FORGE_START_SCRIPT": fake_start_script(tmp_path)})
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=60)
    try:
        body = json.dumps({}).encode("utf-8")
        connection.request("POST", first, body=body,
                           headers={"Content-Type": "application/json"})
        connection.getresponse().read()
        # …and now the *next* request on the same socket.
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200, payload
        assert payload["status"] == "ok"
    finally:
        connection.close()


# ===========================================================================
# the library (Phase 13) — pure units
# ===========================================================================

@pytest.mark.parametrize("name", [
    "../system_prompt", "..\\bridge", "sub/thing", "sub\\thing",
    "%2e%2e", "C:\\Windows", "/etc", "", "   ", None, ".", "..",
])
def test_thumbnail_path_refuses_anything_that_is_not_one_plain_name(name,
                                                                    monkeypatch,
                                                                    tmp_path):
    """Same alphabet gate as the assets and the projects, for the same reason.

    This name arrives in a URL from a browser, and the refusal must not depend
    on path arithmetic.
    """
    monkeypatch.setenv("FORGE_ASSISTANT_THUMBS", str(tmp_path))
    assert bridge.thumbnail_path(name) is None


def test_thumbnail_path_is_one_png_per_project(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_ASSISTANT_THUMBS", str(tmp_path))
    path = bridge.thumbnail_path("eevee-bowl-holder")
    assert path == str(tmp_path / "eevee-bowl-holder.png")
    # One file per project, overwritten: the cache cannot grow past the number
    # of parts, and a stale picture cannot outlive the part it is of.
    assert bridge.thumbnail_path("eevee-bowl-holder") == path


def test_the_thumbnail_cache_lives_beside_uploads_not_in_the_temp_dir(monkeypatch):
    """A preview is regenerated on every click; a thumbnail is what the library
    draws *before* anything is running, so it has to survive a reboot — and be
    somewhere the artist can find and delete, the way ``uploads`` is."""
    monkeypatch.delenv("FORGE_ASSISTANT_THUMBS", raising=False)
    assert (os.path.dirname(bridge.thumbs_dir())
            == os.path.dirname(bridge.uploads_dir()))
    assert os.path.basename(bridge.thumbs_dir()) == "thumbs"
    # …unlike the previews folder, which is pictures of the moment and lives in
    # the temp dir where the OS will eventually sweep it.
    assert bridge.thumbs_dir() != bridge.previews_dir()


def test_spec_components_reads_every_shape_the_block_takes():
    """spec.json is the artist's file and the component tree is still growing.

    A list of names, a list of objects, or a map keyed by name — all three have
    been written, so all three are read.
    """
    found = bridge.spec_components({
        "components": [{"name": "cup_core", "kind": "core",
                        "description": "the dimensioned ring"},
                       "cup_lip"],
        "core": {"base": "the part that holds the bowl"},
        "proposals": ["collar", "ears"],
        "assembly": {"parts": [{"name": "pin", "source": "purchased"}]},
        "_notes": "ignored",
    })
    by_name = {item["name"]: item for item in found}
    assert by_name["cup_core"]["role"] == "core"
    assert by_name["cup_core"]["description"] == "the dimensioned ring"
    # A bare name in a list is a name, with nothing claimed about it.
    assert by_name["cup_lip"]["description"] == ""
    # A map is name -> sentence: the key names it, the string describes it.
    assert by_name["base"]["role"] == "core"
    assert by_name["base"]["description"] == "the part that holds the bowl"
    assert by_name["collar"]["role"] == "proposal"
    assert by_name["pin"]["name"] == "pin"


def test_spec_components_reads_the_tree_the_mcp_server_actually_writes():
    """The shape in `projects/` on this machine, not the one in the fixture.

    ``partforge_new_part`` records the tree as
    ``{"collection", "core", "proposals"}`` — a *tree*, not a map of components.
    Read as a map it yields a chip called "collection", a chip called "core",
    and silently drops every proposal, which is the one thing on the card that
    says what the part is made of.
    """
    found = bridge.spec_components({"components": {
        "collection": "litwick-lamp", "core": "litwick-lamp",
        "proposals": ["litwick-lamp-flame", "litwick-lamp-wax"]}})
    assert [(c["name"], c["role"]) for c in found] == [
        ("litwick-lamp", "core"),
        ("litwick-lamp-flame", "proposal"),
        ("litwick-lamp-wax", "proposal"),
    ]
    # The collection is where the pieces live in the outliner, not a piece.
    assert "collection" not in [c["name"] for c in found]
    # A tree with nothing proposed yet is just the core.
    assert bridge.spec_components({"components": {
        "collection": "cup", "core": "cup", "proposals": []}}) == [
        {"name": "cup", "role": "core", "description": ""}]


def test_the_tree_shape_is_read_off_the_writer_that_makes_it():
    """Pinned against `forge_mcp.util.component_block`, in that file.

    Three keys, and the library reads all three by name — so a rename on either
    side fails here rather than quietly emptying every card's chip row.
    """
    path = os.path.join(REPO_ROOT, "mcp", "forge_mcp", "util.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert ('return {"collection": slug, "core": slug, "proposals": proposals}'
            in source)
    assert bridge._TREE_KEYS == frozenset(("collection", "core", "proposals"))


def test_a_map_keyed_by_name_is_still_read_as_one(monkeypatch):
    """The tree is recognised by its *whole* key set, not by one key.

    ``{"core": "the ring", "collar": "scrap me"}`` is a map of name -> sentence
    that happens to have a piece called "core" in it, and reading it as a tree
    would lose the collar.
    """
    found = bridge.spec_components({"components": {
        "core": "the dimensioned ring", "collar": "scrap me freely"}})
    by_name = {item["name"]: item for item in found}
    assert set(by_name) == {"core", "collar"}
    assert by_name["collar"]["description"] == "scrap me freely"


def test_spec_components_is_empty_rather_than_an_exception_on_junk():
    for spec in (None, {}, {"components": "not a list"}, {"components": 4},
                 {"assembly": "nope"}):
        assert bridge.spec_components(spec) == []


def write_export(folder, name, size=2048, ago=0.0):
    directory = folder / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x00" * size)
    if ago:
        stamp = time.time() - ago
        os.utime(str(path), (stamp, stamp))
    return path


def test_project_exports_lists_the_newest_first_with_its_size(projects):
    folder = projects("cup")
    write_export(folder, "cup.stl", size=4096, ago=600)
    write_export(folder, "cup.3mf", size=1024)
    (folder / "exports" / "subfolder").mkdir()
    found = bridge.project_exports(str(folder))
    assert [item["file"] for item in found] == ["cup.3mf", "cup.stl"]
    assert found[0]["size"] == 1024 and found[1]["size"] == 4096
    assert found[0]["path"].endswith(os.path.join("exports", "cup.3mf"))


def test_project_exports_of_a_project_that_never_exported_is_empty(projects):
    assert bridge.project_exports(str(projects("cup"))) == []


def test_param_count_never_asks_the_service(monkeypatch, projects):
    """A library of twenty parts must not be twenty /parse_params round trips.

    (Nor twenty error cards when the shape service is stopped.) So the count
    comes off the spec, or off a schema the workbench already parsed, or it is
    honestly unknown.
    """
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    folder = projects("cup")
    entry = bridge.project_entry(str(folder))
    assert bridge.param_count_for(entry) == (3, "spec")

    # No spec: unread, not zero — "0 dimensions" would be a lie about a script
    # that has three.
    bare = projects("plain", spec=None)
    count, source = bridge.param_count_for(bridge.project_entry(str(bare)))
    assert count is None and source == "unread"


def test_save_thumbnail_writes_one_file_and_the_folder_ignores_itself(monkeypatch,
                                                                      tmp_path):
    monkeypatch.setenv("FORGE_ASSISTANT_THUMBS", str(tmp_path / "thumbs"))
    render = write_png(tmp_path / "render.png")
    cached = bridge.save_thumbnail("cup", render)
    assert cached == str(tmp_path / "thumbs" / "cup.png")
    with open(cached, "rb") as handle:
        assert handle.read() == PNG
    # The default folder lives in the repo, so it excludes itself rather than
    # making every clone edit .gitignore — a cache of pictures is not source.
    ignore = tmp_path / "thumbs" / ".gitignore"
    assert ignore.is_file() and ignore.read_text(encoding="utf-8").strip() == "*"

    # Rendered again: same file, new bytes.
    again = write_png(tmp_path / "again.png", PNG + b"\x01")
    bridge.save_thumbnail("cup", again)
    assert len([p for p in (tmp_path / "thumbs").iterdir()
                if p.suffix == ".png"]) == 1
    assert bridge.read_thumbnail("cup")[0].endswith(b"\x01")


def test_save_thumbnail_refuses_a_name_that_is_not_a_project(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_ASSISTANT_THUMBS", str(tmp_path / "thumbs"))
    render = write_png(tmp_path / "render.png")
    assert bridge.save_thumbnail("../evil", render) is None
    assert bridge.save_thumbnail("cup", "") is None


def test_remember_preview_only_caches_a_picture_of_exactly_one_part(monkeypatch,
                                                                     tmp_path,
                                                                     projects):
    """A whole-scene render is nobody's thumbnail.

    A picture captioned with the wrong part is worse than no picture: the
    library is how the artist finds their work.
    """
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(projects.root))
    monkeypatch.setenv("FORGE_ASSISTANT_THUMBS", str(tmp_path / "thumbs"))
    projects("cup")
    render = write_png(tmp_path / "render.png")

    assert bridge.remember_preview(["cup"], render) is not None
    assert bridge.read_thumbnail("cup") is not None
    # …and none of these is a picture of one part.
    assert bridge.remember_preview([], render) is None
    assert bridge.remember_preview(["cup", "cup_collar"], render) is None
    assert bridge.remember_preview(["something_else"], render) is None


# ===========================================================================
# the library — GET /library
# ===========================================================================

def library_blender(objects=("cup",), written=None):
    """A fake add-on that can be asked what is in the scene and to render it."""
    scene = {
        "objects": [{"name": name, "type": "MESH",
                     "dimensions": [0.152, 0.152, 0.062], "vertex_count": 4212}
                    for name in objects],
        "active": objects[0] if objects else None,
    }

    def render(request):
        params = request.get("params") or {}
        path = params.get("path")
        if written is not None:
            written.append(path)
        write_png(path)
        return {"path": path, "objects": params.get("objects") or [],
                "view": params.get("view") or "iso", "resolution": 768}

    return by_type({"get_scene_info": scene, "render_preview": render})


def test_library_is_empty_before_anything_is_made(client):
    status, body = client.request("/library", timeout=40)
    assert status == 200
    assert body["projects"] == [] and body["count"] == 0
    assert body["dir"].endswith("projects")
    # The scene block is present even with nothing running: it is how the page
    # knows to print one sentence instead of drawing an empty row.
    assert body["scene"]["ok"] is False


def test_library_carries_everything_a_card_draws(bridges, projects):
    folder = projects("cup")
    write_export(folder, "cup.stl", size=4096, ago=600)
    write_export(folder, "cup.3mf", size=1024)
    client = bridges()

    status, body = client.request("/library", timeout=40)
    assert status == 200, body
    assert body["count"] == 1
    card = body["projects"][0]

    assert card["name"] == "cup"
    assert card["object"] == "cup"          # the naming convention, not "part"
    assert card["script"] == "part.py"
    assert card["description"] == "A cup that holds a thing."
    assert card["param_count"] == 3 and card["param_source"] == "spec"
    assert [c["name"] for c in card["components"]] == ["cup_core", "cup_collar"]
    assert card["components"][1]["role"] == "proposal"
    assert card["features"] == ["a fluted band", "four feet"]
    assert card["export_count"] == 2
    assert [e["file"] for e in card["exports"]] == ["cup.3mf", "cup.stl"]
    assert card["exports"][0]["size"] == 1024
    assert card["mtime"] > 0
    # No picture has been taken yet, so the card is a placeholder.
    assert card["has_thumbnail"] is False
    assert card["thumbnail_url"] == "/projects/cup/thumbnail"
    # …and no scene file either, which is the ordinary state of a new project.
    assert card["has_blend"] is False
    assert card["blend_size"] == 0 and card["blend_mtime"] == 0.0
    assert card["blend_path"].endswith(os.path.join("cup", "cup.blend"))


def test_a_card_chips_the_core_and_the_proposals_a_real_spec_records(bridges,
                                                                     projects):
    """End to end, on the shape `partforge_new_part` writes into `projects/`."""
    projects("litwick-lamp", extra={"components": {
        "collection": "litwick-lamp", "core": "litwick-lamp",
        "proposals": ["litwick-lamp-flame"]}})
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert [(c["name"], c["role"]) for c in card["components"]] == [
        ("litwick-lamp", "core"), ("litwick-lamp-flame", "proposal")]


def test_library_lists_every_project_and_skips_what_is_not_one(bridges, projects):
    projects("cup")
    projects("lid", script="part_lid.py")
    (projects.root / "notes").mkdir()
    (projects.root / "notes" / "todo.txt").write_text("later", encoding="utf-8")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    assert [p["name"] for p in body["projects"]] == ["cup", "lid"]


def test_library_survives_a_spec_that_will_not_parse(bridges, projects):
    """A trailing comma in the artist's own file must not hide their part."""
    folder = projects("cup")
    (folder / "spec.json").write_text("{ not json,", encoding="utf-8")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert card["name"] == "cup" and card["spec"] is None
    assert card["description"] == "" and card["components"] == []
    # The script is still readable, so the card still says it has dimensions.
    assert card["has_params"] is True
    assert card["param_count"] is None


def test_library_still_draws_with_blender_closed(bridges, projects):
    """The whole point: a shelf of your work is a folder read.

    Everything except the pictures survives every service on the machine being
    stopped, and the one thing that does not is one sentence with one thing to
    do in it.
    """
    projects("cup")
    client = bridges()          # nothing is listening on any of the ports
    status, body = client.request("/library", timeout=40)
    assert status == 200, body
    assert body["projects"][0]["name"] == "cup"
    scene = body["scene"]
    assert scene["ok"] is False and scene["blender"] is False
    assert "Blender is not running" in scene["error"]
    assert "press N" in scene["error"] and "Start Server" in scene["error"]
    assert scene["objects"] == []


def test_library_shows_works_in_progress_beside_the_saved_parts(bridges, projects,
                                                                fake_blender):
    projects("cup")
    blender = fake_blender(library_blender(objects=("cup", "sculpt_head")))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    _status, body = client.request("/library", timeout=40)
    scene = body["scene"]
    assert scene["ok"] is True and scene["count"] == 2
    assert [o["name"] for o in scene["objects"]] == ["cup", "sculpt_head"]
    # A sculpt has no folder in projects/ and is still the artist's work.
    assert [p["name"] for p in body["projects"]] == ["cup"]


def test_library_says_so_when_there_is_no_projects_folder_at_all(bridges,
                                                                 tmp_path):
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(tmp_path / "nope")})
    _status, body = client.request("/library", timeout=40)
    assert body["projects"] == []
    assert "no projects folder" in body["note"]


# ===========================================================================
# the library — the thumbnail cache
# ===========================================================================

def test_a_thumbnail_is_a_404_until_one_is_taken(bridges, projects):
    projects("cup")
    client = bridges()
    status, _headers, body = raw_get(client, "/projects/cup/thumbnail")
    assert status == 404
    # …and it says how to get one, because this 404 is a placeholder card
    # rather than a fault.
    assert b"Preview" in body


def test_a_thumbnail_is_photographed_from_the_scene_and_then_cached(bridges,
                                                                    projects,
                                                                    fake_blender,
                                                                    tmp_path):
    projects("cup")
    written = []
    blender = fake_blender(library_blender(("cup",), written))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})

    assert raw_get(client, "/projects/cup/thumbnail")[0] == 404

    status, body = client.request("/projects/cup/thumbnail", payload={},
                                  timeout=60)
    assert status == 200, body
    assert body["project"] == "cup" and body["object"] == "cup"
    assert body["cached"] is True
    assert body["url"] == "/file/" + body["token"]
    # The render went to a path this bridge chose, and only the one object.
    assert os.path.dirname(written[0]) == str(tmp_path / "previews")
    types = [request["type"] for request in blender.seen]
    assert types == ["get_scene_info", "render_preview"], types
    assert blender.seen[1]["params"]["objects"] == ["cup"]

    # …and now the cache answers, which is what the card draws on a page that
    # is opened with Blender closed.
    status, headers, png = raw_get(client, "/projects/cup/thumbnail")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert png == PNG
    assert os.path.isfile(str(tmp_path / "thumbs" / "cup.png"))

    _status, listing = client.request("/library", timeout=40)
    card = listing["projects"][0]
    assert card["has_thumbnail"] is True and card["thumbnail_mtime"] > 0


def test_a_thumbnail_never_builds_the_part_to_photograph_it(bridges, projects,
                                                            fake_blender):
    """A picture is not a reason to run somebody's script.

    Generating the part when it is missing would make a page of twelve cards
    into twelve rebuilds — minutes of their machine and a scene they did not
    ask to have changed, for pictures. So it is a 409 naming the button that
    builds it, and the artist stays the one who decides when geometry happens.
    """
    projects("cup")
    blender = fake_blender(library_blender(objects=("something_else",)))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/thumbnail", payload={},
                                  timeout=60)
    assert status == 409, body
    assert "not in the Blender scene" in body["error"]
    assert "Apply & rebuild" in body["error"]
    assert body["object"] == "cup"
    assert body["scene_objects"] == ["something_else"]
    # Nothing was rendered, and nothing was built.
    assert [request["type"] for request in blender.seen] == ["get_scene_info"]


def test_a_thumbnail_with_blender_closed_names_the_button_to_press(bridges,
                                                                   projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/cup/thumbnail", payload={},
                                  timeout=40)
    assert status == 503, body
    assert body["blender"] is False
    assert "Blender is not running" in body["error"]
    assert "Start Server" in body["error"]


def test_a_project_with_no_part_script_has_nothing_to_photograph(bridges,
                                                                 projects,
                                                                 fake_blender):
    """A spec and no script is a plan, not a part.

    422 rather than 409: the 409 names a button that would build it, and there
    is nothing here to build yet.  Blender is never asked.
    """
    projects("plan", script="")
    blender = fake_blender(library_blender(("cup",)))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/plan/thumbnail", payload={},
                                  timeout=40)
    assert status == 422, body
    assert "no part script" in body["error"]
    assert body["project"] == "plan"
    assert blender.seen == []
    # …and the card still draws, with a placeholder where the picture goes.
    _status, listing = client.request("/library", timeout=40)
    card = [p for p in listing["projects"] if p["name"] == "plan"][0]
    assert card["has_thumbnail"] is False and card["object"] == ""


@pytest.mark.parametrize("name", ["nope", "..", "..%2Fassistant", "sub%2Fthing"])
def test_a_thumbnail_of_a_project_that_is_not_one_is_a_plain_404(bridges,
                                                                 projects, name):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/%s/thumbnail" % name, payload={})
    assert status == 404, body
    assert "No project called" in body["error"]
    # …and the GET half is a 404 too, without saying which kind of miss it was.
    assert raw_get(client, "/projects/%s/thumbnail" % name)[0] == 404


def test_a_thumbnail_post_never_poisons_the_next_request(bridges, projects):
    """The keep-alive rule, on the route most likely to 404 before it drains."""
    import http.client

    projects("cup")
    client = bridges()
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=60)
    try:
        connection.request("POST", "/projects/nope/thumbnail",
                           body=json.dumps({"view": "front"}).encode("utf-8"),
                           headers={"Content-Type": "application/json"})
        assert connection.getresponse().read()
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200, payload
    finally:
        connection.close()


def test_the_workbench_render_doubles_as_the_library_thumbnail(bridges, projects,
                                                               fake_blender):
    """One render, two uses.

    The artist presses Render in the Workbench; the PNG is already on disk, so
    asking Blender to draw the same shape again for a 240-pixel square would be
    work nobody asked for.
    """
    projects("cup")
    blender = fake_blender(library_blender(("cup",)))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})

    status, body = client.request("/preview", payload={"objects": ["cup"]},
                                  timeout=60)
    assert status == 200, body
    assert body["thumbnail_for"] == "cup"
    status, headers, png = raw_get(client, "/projects/cup/thumbnail")
    assert status == 200 and png == PNG
    assert headers["Content-Type"] == "image/png"


def test_a_whole_scene_render_is_nobodys_thumbnail(bridges, projects,
                                                   fake_blender):
    projects("cup")
    blender = fake_blender(library_blender(("cup",)))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/preview", payload={}, timeout=60)
    assert status == 200, body
    assert body["thumbnail_for"] is None
    assert raw_get(client, "/projects/cup/thumbnail")[0] == 404


# ===========================================================================
# the library — the page
# ===========================================================================

def test_the_library_tab_and_its_panel_are_wired_to_each_other(client):
    html = fetch_text(client, "/")
    assert 'aria-controls="panel-library"' in html
    assert 'aria-labelledby="tab-library"' in html
    # It comes after the Studio: the Studio is the working session — asking
    # for a part and editing it — and this is the shelf you look along after.
    assert html.index('id="tab-studio"') < html.index('id="tab-library"')
    assert html.index('id="tab-library"') < html.index('id="tab-flows"')


def test_the_library_is_linkable(client):
    """#library is a URL, so it can be bookmarked and pasted."""
    script = fetch_text(client, "/webui/app.js")
    assert "function tabFromHash()" in script
    assert "hashchange" in script
    assert "replaceState" in script


def test_the_library_cards_carry_their_four_buttons(client):
    script = fetch_text(client, "/webui/app.js")
    assert '"Open in Studio"' in script
    assert "function openInStudio(" in script
    assert "/thumbnail" in script
    # Phase 15: the scene file is what "open this model" means, so Open leads
    # and Save scene is how a project gets one.
    assert '"Open")' in script and "function openProject(" in script
    assert '"Save scene")' in script and "function saveProject(" in script


def test_the_library_panel_carries_both_grids_and_its_refresh(client):
    """The saved parts and the works in progress are two rows, not one list.

    A sculpt with no folder is still the artist's work and belongs on the page;
    it is not, however, a project, and a card that implied it was would send
    somebody looking for a folder that is not there.
    """
    html = fetch_text(client, "/")
    panel = html[html.index('id="panel-library"'):html.index('id="panel-flows"')]
    assert 'id="library"' in panel and 'class="lib-grid"' in panel
    assert 'id="library-scene"' in panel
    assert 'id="library-refresh"' in panel
    assert panel.index('id="library"') < panel.index('id="library-scene"')
    assert "In progress" in panel


def test_the_library_grid_is_responsive_without_a_media_query_per_size(client):
    """One part is a card, twelve are a grid and a narrow window is a column."""
    css = fetch_text(client, "/webui/app.css")
    assert ".lib-grid" in css
    assert "auto-fill" in css and "minmax(" in css
    # The picture area keeps its shape whatever the render was, and never
    # crops the part to fill it.
    assert "aspect-ratio" in css
    assert "object-fit: contain" in css


def test_a_card_with_no_picture_draws_a_placeholder_rather_than_a_broken_image(
        client):
    """A missing thumbnail is the normal state of a part nobody has rendered."""
    script = fetch_text(client, "/webui/app.js")
    assert "function initial(" in script
    assert "lib-initial" in script
    assert "project.has_thumbnail" in script
    css = fetch_text(client, "/webui/app.css")
    assert ".lib-initial" in css


def test_a_cards_picture_is_busted_out_of_the_browser_cache_by_its_mtime(client):
    """The thumbnail URL is stable, so the mtime has to ride in the query.

    A browser showing yesterday's shape under today's name is the one bug this
    feature must not have.
    """
    script = fetch_text(client, "/webui/app.js")
    assert 'project.thumbnail_url + "?t=" + (project.thumbnail_mtime' in script


def test_a_card_says_what_it_is_made_of_and_what_it_exported(client):
    """Description, dimensions, component chips, exports — the folder read."""
    script = fetch_text(client, "/webui/app.js")
    assert "lib-desc" in script and "project.description" in script
    assert "project.param_count" in script and "dimensions" in script
    assert "lib-chip" in script and "project.components" in script
    assert "lib-exports" in script and "function bytes(" in script
    # A proposal is the piece you are invited to scrap, and it does not look as
    # settled as the core does.
    assert "is-proposal" in script
    assert ".lib-chip.is-proposal" in fetch_text(client, "/webui/app.css")


def test_open_in_studio_switches_the_tab_and_the_selection(client):
    """Both halves, in that order — a tab switch that left the picker on the
    previous part would be a button that lies about what it did."""
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function openInStudio("):]
    body = body[:body.index("\n  function ")]
    assert 'showTab("studio")' in body
    assert "selectProject(name)" in body
    # …and it pins, because somebody who went looking for this part in the
    # Library should not lose it to the next thing the conversation does.
    assert "pinProject(name)" in body
    # …and it waits for the picker's own fetch rather than racing a second one.
    assert "whenProjectsLoaded()" in body
    assert "function whenProjectsLoaded()" in script


def test_the_library_is_refetched_every_time_the_tab_is_opened(client):
    """Unlike the flows and the workbench, which are loaded once.

    A part made in the chat a minute ago is exactly what somebody opens this
    tab to look for, and it costs one folder read.
    """
    script = fetch_text(client, "/webui/app.js")
    assert 'if (which === "library") { loadLibrary(); }' in script
    assert "function loadLibrary()" in script
    assert 'api("/library")' in script


# ===========================================================================
# Phase 15 — the project's own .blend, and opening it from the Library
# ===========================================================================

#: Blender's own magic bytes, so nothing here has to pretend a text file is a
#: scene.  The size is what the card prints; the content is never parsed.
BLEND_MAGIC = b"BLENDER-v500" + b"\x00" * 64


def write_blend(folder, name=None, data=BLEND_MAGIC):
    """Put a ``<name>.blend`` in a project folder, the way a save would."""
    name = name or os.path.basename(str(folder))
    path = os.path.join(str(folder), "%s.blend" % name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


#: A stand-in for ``blender.exe``: it records the argv it was launched with and
#: exits.  ``FORGE_BLENDER_EXE`` pointing at a ``.py`` runs under this
#: interpreter (the ``start_forge.ps1`` trick), which is what lets the spawn be
#: proved without a Blender window appearing on somebody's screen.
FAKE_BLENDER = """
import json, os, sys
log = os.environ.get("FORGE_FAKE_BLENDER_LOG")
if log:
    with open(log, "w", encoding="utf-8") as handle:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, handle)
"""


@pytest.fixture
def fake_blender_exe(tmp_path):
    """``(exe_path, log_path)`` for a Blender that only writes down its argv."""
    exe = tmp_path / "fake_blender.py"
    exe.write_text(FAKE_BLENDER, encoding="utf-8")
    return str(exe), str(tmp_path / "blender-launch.json")


def blend_blender(saved=None, opened=None, needs_confirmation=False):
    """A fake add-on that answers the two Phase 15 commands."""
    def save(request):
        params = request.get("params") or {}
        if saved is not None:
            saved.append(params)
        name = str(params.get("project") or "")
        return {"project": name, "path": "/somewhere/%s.blend" % name,
                "object_count": 3, "replaced": False, "created_folder": False,
                "session_file": "", "retargeted": False}

    def open_(request):
        params = request.get("params") or {}
        if opened is not None:
            opened.append(params)
        if needs_confirmation and not params.get("confirm"):
            return {"project": params.get("name"), "opened": False,
                    "needs_confirmation": True, "dirty": True,
                    "object_count": 4, "session_file": "",
                    "would_lose": "This session has never been saved (4 "
                                  "object(s) in the scene).",
                    "hint": "Press Save scene to project first."}
        return {"project": params.get("name"), "opened": True,
                "needs_confirmation": False, "confirmed": bool(params.get("confirm")),
                "object_count": 7, "session_file": "/somewhere/cup.blend",
                "server_running": True, "server_port": 9876,
                "pump_survived": True}

    return by_type({"save_project_blend": save, "open_project_blend": open_,
                    "get_scene_info": {"objects": []}})


# -- pure units -------------------------------------------------------------

def test_project_blend_is_a_stat_and_nothing_else(projects):
    """The library must draw with Blender closed, so this asks the disk."""
    folder = projects("cup")
    absent = bridge.project_blend(str(folder))
    assert absent["exists"] is False
    assert absent["size"] == 0 and absent["mtime"] == 0.0
    assert absent["path"].endswith(os.path.join("cup", "cup.blend"))

    write_blend(folder)
    found = bridge.project_blend(str(folder))
    assert found["exists"] is True
    assert found["size"] == len(BLEND_MAGIC)
    assert found["mtime"] > 0


def test_a_saved_scene_counts_as_touching_the_project(projects):
    """Saving a sculpt is work, so it moves the card's "last touched"."""
    folder = projects("cup")
    entry = bridge.project_entry(str(folder))
    before = bridge.project_mtime(str(folder), entry, [])
    write_blend(folder)
    blend = bridge.project_blend(str(folder))
    os.utime(blend["path"], (before + 500, before + 500))
    blend = bridge.project_blend(str(folder))
    after = bridge.project_mtime(str(folder), entry, [], blend)
    assert after > before


def test_resolve_blender_takes_the_override_first(monkeypatch, tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("FORGE_BLENDER_EXE", str(exe))
    assert bridge.resolve_blender() == str(exe)
    # A path that is not there is not silently swapped for whatever is on PATH
    # under some *other* name: the override is either a file or a command.
    monkeypatch.setenv("FORGE_BLENDER_EXE", str(tmp_path / "nope.exe"))
    assert bridge.resolve_blender() is None


def test_blender_launch_argv_runs_a_py_stand_in_under_this_interpreter(tmp_path):
    blend = str(tmp_path / "cup.blend")
    argv = bridge.blender_launch_argv(str(tmp_path / "fake.py"), blend)
    assert argv[0] == sys.executable
    assert argv[-1] == blend
    real = bridge.blender_launch_argv(r"C:\Blender\blender.exe", blend)
    assert real == [r"C:\Blender\blender.exe", blend]


@pytest.mark.skipif(os.name != "nt", reason="creation flags are Windows only")
def test_the_gui_spawn_is_detached_and_never_windowless():
    """The one spawn in this file that must *show* something.

    Every other subprocess here is a background helper and carries
    ``CREATE_NO_WINDOW``.  This one is an application the artist is about to
    work in, and the two flags are mutually exclusive to ``CreateProcess``
    anyway — so getting it wrong is not a cosmetic bug, it is a Blender that
    never appears.
    """
    flags = bridge.spawn_creationflags()
    assert flags & subprocess.DETACHED_PROCESS
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (flags & bridge.CREATE_NO_WINDOW)


def test_spawn_blender_starts_the_stand_in_and_hands_it_the_file(
        monkeypatch, tmp_path, fake_blender_exe):
    exe, log = fake_blender_exe
    monkeypatch.setenv("FORGE_FAKE_BLENDER_LOG", log)
    blend = str(tmp_path / "cup.blend")
    proc = bridge.spawn_blender(exe, blend)
    try:
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    with open(log, encoding="utf-8") as handle:
        seen = json.load(handle)
    assert seen["argv"] == [blend]


# -- the library card -------------------------------------------------------

def test_a_card_says_whether_the_project_has_a_scene_of_its_own(bridges,
                                                                 projects):
    folder = projects("cup")
    write_blend(folder)
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert card["has_blend"] is True
    assert card["blend_size"] == len(BLEND_MAGIC)
    assert card["blend_mtime"] > 0
    assert card["blend_path"] == os.path.join(str(folder), "cup.blend")


# -- POST /projects/<name>/save --------------------------------------------

def test_save_writes_the_scene_into_the_project(bridges, projects,
                                                fake_blender):
    projects("cup")
    saved = []
    blender = fake_blender(blend_blender(saved=saved))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})

    status, body = client.request("/projects/cup/save", payload={}, timeout=60)
    assert status == 200, body
    assert body["saved"] is True and body["project"] == "cup"
    assert saved == [{"project": "cup"}]
    # The route never names a path of its own: the folder convention lives in
    # one place, and it is the add-on's.
    assert [request["type"] for request in blender.seen] == ["save_project_blend"]
    # And the answer carries the one reassurance worth carrying.
    assert "session_file" in body


def test_save_of_a_project_that_is_not_one_is_a_404(bridges, projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/../bridge/save", payload={},
                                  timeout=40)
    assert status == 404, body


def test_save_with_blender_closed_names_the_button_to_press(bridges, projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/cup/save", payload={}, timeout=40)
    assert status == 503, body
    assert body["blender"] is False
    assert "Blender is not running" in body["error"]
    assert "Start Server" in body["error"]


def test_save_passes_the_addons_own_refusal_through(bridges, projects,
                                                    fake_blender):
    projects("cup")
    blender = fake_blender(by_type({
        "save_project_blend": "Could not create the project folder: read-only"}))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/save", payload={}, timeout=40)
    assert status == 502, body
    assert body["blender"] is True
    assert "read-only" in body["error"]


# -- POST /projects/<name>/open --------------------------------------------

def test_open_with_no_blend_never_touches_blender_at_all(bridges, projects,
                                                         fake_blender):
    """The ordinary state of a new project is not a fault.

    There is nothing to open, so the answer is the thing that *does* exist —
    its dimensions — plus one sentence naming the button that makes the other.
    Blender is not asked, because the question was answered by a ``stat``.
    """
    projects("cup")
    blender = fake_blender(blend_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})
    status, body = client.request("/projects/cup/open", payload={}, timeout=40)
    assert status == 200, body
    assert body["route"] == "no_blend"
    assert body["opened"] is False and body["has_blend"] is False
    assert "Save scene to project" in body["hint"]
    assert body["object"] == "cup"
    assert blender.seen == []


def test_open_routes_through_the_running_blender_and_asks_first(bridges,
                                                                projects,
                                                                fake_blender):
    folder = projects("cup")
    write_blend(folder)
    opened = []
    blender = fake_blender(blend_blender(opened=opened, needs_confirmation=True))
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port)})

    status, body = client.request("/projects/cup/open", payload={}, timeout=60)
    assert status == 200, body           # a question is not an error
    assert body["route"] == "running"
    assert body["needs_confirmation"] is True and body["opened"] is False
    assert "object(s)" in body["would_lose"]
    assert opened == [{"name": "cup", "confirm": False}]

    # …and the second press carries the confirmation the artist just gave.
    status, body = client.request("/projects/cup/open", payload={"confirm": True},
                                  timeout=60)
    assert status == 200, body
    assert body["opened"] is True and body["needs_confirmation"] is False
    assert opened[-1] == {"name": "cup", "confirm": True}


def test_a_running_blender_always_wins_over_spawning_a_second_one(
        bridges, projects, fake_blender, fake_blender_exe, tmp_path):
    """Two instances would fight over port 9876, so one is never started."""
    folder = projects("cup")
    write_blend(folder)
    exe, log = fake_blender_exe
    blender = fake_blender(blend_blender())
    client = bridges(env_extra={"FORGE_BLENDER_PORT": str(blender.port),
                                "FORGE_BLENDER_EXE": exe,
                                "FORGE_FAKE_BLENDER_LOG": log})
    status, body = client.request("/projects/cup/open", payload={"confirm": True},
                                  timeout=60)
    assert status == 200, body
    assert body["route"] == "running"
    assert not os.path.exists(log), "a second Blender was started"


def test_open_with_blender_closed_starts_one_on_the_file(bridges, projects,
                                                          fake_blender_exe,
                                                          tmp_path):
    folder = projects("cup")
    blend = write_blend(folder)
    exe, log = fake_blender_exe
    client = bridges(env_extra={"FORGE_BLENDER_EXE": exe,
                                "FORGE_FAKE_BLENDER_LOG": log})

    status, body = client.request("/projects/cup/open", payload={}, timeout=60)
    assert status == 200, body
    assert body["route"] == "spawned"
    assert body["opened"] is True and body["pid"] > 0
    assert body["executable"] == exe
    assert "Start Server" in body["note"]

    deadline = time.time() + 30
    while time.time() < deadline and not os.path.exists(log):
        time.sleep(0.1)
    assert os.path.exists(log), "the stand-in Blender never ran"
    with open(log, encoding="utf-8") as handle:
        seen = json.load(handle)
    assert seen["argv"] == [blend]


def test_open_with_no_blender_installed_says_what_to_set(bridges, projects,
                                                          tmp_path):
    folder = projects("cup")
    write_blend(folder)
    client = bridges(env_extra={"FORGE_BLENDER_EXE": str(tmp_path / "nope.exe"),
                                "PATH": str(tmp_path)})
    status, body = client.request("/projects/cup/open", payload={}, timeout=40)
    assert status == 501, body
    assert body["route"] == "no_blender"
    assert "FORGE_BLENDER_EXE" in body["error"]
    assert body["has_blend"] is True


def test_open_of_a_project_that_is_not_one_is_a_404(bridges, projects):
    projects("cup")
    client = bridges()
    status, body = client.request("/projects/..%2Fbridge/open", payload={},
                                  timeout=40)
    assert status == 404, body


def test_an_open_body_never_poisons_the_next_request(bridges, projects):
    """The keep-alive drain, on the two routes that take a body and may 404."""
    projects("cup")
    client = bridges()
    for path in ("/projects/nope/open", "/projects/nope/save"):
        status, _body = client.request(path, payload={"confirm": True},
                                       timeout=40)
        assert status == 404
        status, body = client.request("/health", timeout=40)
        assert status == 200, body


# -- the page ---------------------------------------------------------------

def test_the_page_asks_before_it_throws_a_scene_away(client):
    """A file load cannot be undone, so the confirmation is the last chance."""
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function openProject("):]
    body = body[:body.index("\n  function ")]
    assert "window.confirm(" in body
    assert "data.would_lose" in body
    assert "Undo does not cross a file load" in body
    # …and the confirmed press is the same call with the answer attached.
    assert "openProject(project, card, true)" in body


def test_the_page_branches_on_the_route_not_the_status(client):
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function openProject("):]
    body = body[:body.index("\n  function ")]
    assert 'data.route === "no_blend"' in body
    assert 'data.route === "spawned"' in body
    # No .blend falls back to the thing that does exist: its dimensions.
    assert "openInStudio(project.name)" in body


def test_the_card_says_whether_there_is_a_scene_file(client):
    script = fetch_text(client, "/webui/app.js")
    assert "function blendFact(" in script
    assert "no scene file yet" in script
    assert "project.has_blend" in script
    assert "lib-blend" in script
    css = fetch_text(client, "/webui/app.css")
    assert ".lib-actions .lib-open" in css


# ===========================================================================
# the Studio (Phase 14) — one screen for one working session
# ===========================================================================
#
# The artist's own words for what was wrong with Phase 11: *"I want to directly
# edit this from the UI — I should be able to pinch or stretch the bottom radius
# by typing into a param box or flatten the top with params rather than going
# back and forth with the AI… I don't like the multiple tabs for one workflow
# session — one screen that allows me to control it directly."*  Three edits
# asked of the model took 155 seconds and $0.41; the same three numbers typed
# into the rail are one rebuild each.

def studio_panel(client):
    """The Studio panel's markup, on its own."""
    html = fetch_text(client, "/")
    return html[html.index('id="panel-studio"'):html.index('id="panel-library"')]


def test_the_studio_holds_the_conversation_and_the_sheet_on_one_screen(client):
    """The whole point of the merge: neither half is a tab away from the other.

    Both sets of anchors are inside the one panel, so there is no arrangement
    of tabs in which the artist can see the answer about a dimension and not
    the box that changes it.
    """
    panel = studio_panel(client)
    for anchor in ("thread", "composer", "message", "send", "model",
                   "composer-status"):
        assert ('id="%s"' % anchor) in panel, "the chat's #%s left the Studio" % anchor
    for anchor in ("project", "wb-params", "wb-apply", "wb-reset", "wb-status",
                   "wb-preview", "wb-view", "wb-preview-refresh", "scene",
                   "scene-refresh", "wb-sheet", "projects-refresh"):
        assert ('id="%s"' % anchor) in panel, "the sheet's #%s left the Studio" % anchor


def test_the_conversation_comes_first_and_the_rail_second(client):
    """Source order is the stacking order at a narrow width, and chat leads."""
    panel = studio_panel(client)
    assert panel.index('id="studio-chat"') < panel.index('id="studio-rail"')
    assert panel.index('id="thread"') < panel.index('id="wb-params"')


def test_the_rail_is_a_column_that_scrolls_on_its_own(client):
    """Typing into a parameter must never move the conversation, or vice versa."""
    css = fetch_text(client, "/webui/app.css")
    studio = css[css.index(".studio {"):css.index(".rail-head")]
    assert "grid-template-columns" in studio
    assert "overflow-y: auto" in studio          # the rail's own scrollbar
    assert "min-height: 0" in studio             # …which a grid child needs


def test_the_studio_stacks_below_a_breakpoint(client):
    """A laptop and a second monitor are not the same window.

    Below the breakpoint the two columns become one, and the chat is on top
    because that is where a part comes into being.
    """
    css = fetch_text(client, "/webui/app.css")
    assert "@media (max-width: 1100px)" in css
    block = css[css.index("@media (max-width: 1100px)"):]
    block = block[:block.index("@media (max-width: 720px)")]
    assert ".studio" in block and "flex-direction: column" in block
    assert ".studio-rail" in block


def test_enter_in_a_value_box_is_apply(client):
    """The point of the rail is typing speed: 1.5, Enter, rebuilt.

    Reaching for a button between every field is the thing that made asking
    the model feel no slower than doing it yourself.
    """
    script = fetch_text(client, "/webui/app.js")
    assert "function applyOnEnter(" in script
    hook = script[script.index("function applyOnEnter("):]
    hook = hook[:hook.index("\n    }") + 6]
    assert 'event.key !== "Enter"' in hook
    assert "applyParams()" in hook
    assert "event.preventDefault()" in hook
    # …wired to both kinds of box the sheet draws, not only the number.
    assert "applyOnEnter(number);" in script
    assert "applyOnEnter(text);" in script


def test_the_rail_says_how_long_the_rebuild_took(client):
    """The number that makes the case for the direct path.

    155 seconds and $0.41 through the model; this says "1.8 s" and costs
    nothing, right beside the button that did it.
    """
    html = fetch_text(client, "/")
    assert 'id="wb-timing"' in html
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function applyParams()"):]
    body = body[:body.index("\n  function ")]
    assert "Date.now()" in body
    assert '$("wb-timing").textContent' in body
    assert '.toFixed(1) + " s"' in body


def test_a_held_down_enter_does_not_queue_a_second_rebuild(client):
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function applyParams()"):]
    body = body[:body.index("\n  function ")]
    assert "if (button.disabled) { return Promise.resolve(); }" in body


def test_the_old_tab_names_land_on_the_studio(client):
    """A bookmark of #chat and a localStorage of "workbench" both still work.

    The two tabs became one screen; somebody's browser does not know that, and
    a stored name from yesterday must not drop them on a default by accident.
    """
    script = fetch_text(client, "/webui/app.js")
    assert 'var TAB_ALIASES = { chat: "studio", workbench: "studio" };' in script
    assert "function storedTab()" in script
    body = script[script.index("function storedTab()"):]
    body = body[:body.index("\n  // ---")]
    assert 'localStorage.getItem("forge.tab")' in body
    # …and the alias is written back, so it is read exactly once per browser.
    assert 'localStorage.setItem("forge.tab", wanted)' in body


def test_the_rail_opens_on_the_part_the_page_was_left_on(client):
    """Else the most recently modified project, which /library is the only
    route to carry an mtime for."""
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function initialProject()"):]
    body = body[:body.index("\n  function ")]
    assert 'localStorage.getItem("forge.project")' in body
    assert 'api("/library")' in body
    assert "mtime" in body


def test_picking_a_part_by_hand_pins_it_until_the_conversation_moves_on(client):
    script = fetch_text(client, "/webui/app.js")
    assert "function pinProject(" in script
    assert "function followTo(" in script
    assert "function considerFollowing(" in script
    # A detection always wins over a pin: the artist asked for this part in
    # the very message that produced it.
    body = script[script.index("function followTo("):]
    body = body[:body.index("\n  function ")]
    assert "follow.pinned = null" in body
    assert "loadProjects(name)" in body          # a brand-new part, one refetch


def test_a_page_load_does_not_yank_the_rail_to_an_old_turn(client):
    """The conversation redrawn at startup is history, not a decision."""
    script = fetch_text(client, "/webui/app.js")
    assert "historical: true" in script
    body = script[script.index("function upsert(job, options)"):]
    body = body[:body.index("\n  function ")]
    assert "considerFollowing(job)" in body


# ---------------------------------------------------------------------------
# auto-follow, run for real under node
# ---------------------------------------------------------------------------

FOLLOW_HARNESS = """
const fs = require('fs');
globalThis.window = globalThis;
eval(fs.readFileSync(process.argv[2], 'utf8'));
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
process.stdout.write(JSON.stringify(cases.map(
  function (one) {
    return window.ForgeFollow.projectFromJob(one.job, one.projects);
  })));
"""

#: What the picker knows about: two parts whose script is the usual `part.py`
#: and one whose script names itself.
KNOWN = [
    {"name": "cup", "script": "part.py", "scripts": ["part.py"]},
    {"name": "litwick-lamp", "script": "part.py", "scripts": ["part.py"]},
    {"name": "ring", "script": "part_base_ring.py",
     "scripts": ["part_base_ring.py"]},
]


def tool_line(label):
    return {"kind": "tool", "label": label}


def canned_job(activity=(), reply="", message="", state="done"):
    return {"job_id": "j1", "state": state, "activity": list(activity),
            "reply": reply, "message": message}


def follow_choices(tmp_path, cases):
    """Run follow.js over some canned /jobs entries; hand back its answers."""
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("no node on this machine to run follow.js with")
    harness = tmp_path / "follow-harness.js"
    harness.write_text(FOLLOW_HARNESS, encoding="utf-8")
    payload = tmp_path / "follow-cases.json"
    payload.write_text(json.dumps(list(cases)), encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "follow.js"), str(payload)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


#: Every rule in follow.js, as a job the bridge could really have published.
#: The labels are `bridge.tool_label()`'s exact output shape — a short tool
#: name, a colon, and the most identifying argument with paths basenamed.
FOLLOW_CASES = [
    ("a new part is named in plain words and slugged to its folder",
     canned_job([tool_line("partforge_new_part: a small magnet holder")]),
     "a-small-magnet-holder"),
    ("…and when that folder is already in the picker, it is that entry",
     canned_job([tool_line("partforge_new_part: Litwick Lamp")]), "litwick-lamp"),
    ("a script filename only one part has names that part",
     canned_job([tool_line("partforge_open_in_panel: part_base_ring.py")]), "ring"),
    ("a script filename half the parts share names none of them",
     canned_job([tool_line("partforge_open_in_panel: part.py")]), ""),
    ("…unless the reply says which folder it was in",
     canned_job([tool_line("partforge_generate: part.py")],
         reply="Rebuilt projects/cup/part.py at 3 mm."), "cup"),
    ("a Windows path in the reply reads the same as a POSIX one",
     canned_job(reply="Wrote C:\\forge\\projects\\litwick-lamp\\part.py."),
     "litwick-lamp"),
    ("the newest part-shaped tool wins, not the first",
     canned_job([tool_line("partforge_new_part: cup"),
          tool_line("partforge_open_in_panel: part_base_ring.py")]), "ring"),
    ("tools that are not about opening a part do not move the sheet",
     canned_job([tool_line("partforge_check: part.py"),
                 tool_line("Read: architecture.md")]), ""),
    ("a job that has not finished has not decided anything",
     canned_job([tool_line("partforge_new_part: a small magnet holder")], state="running"),
     ""),
    ("a project's name is a name, not a substring",
     canned_job(reply="I put it in the cupboard, so to speak."), ""),
    ("a folder the picker has not heard of is still a folder",
     canned_job(reply="Written to projects/hex-mug/part.py."), "hex-mug"),
    ("nothing at all is nothing at all",
     canned_job(reply="I would start with the wall thickness."), ""),
    ("a question that names a part, when the answer names none",
     canned_job(message="make the litwick-lamp taller"), "litwick-lamp"),
]


def test_auto_follow_reads_a_finished_job_the_way_the_contract_says(tmp_path):
    cases = [{"job": entry, "projects": KNOWN} for _why, entry, _want in
             FOLLOW_CASES]
    found = follow_choices(tmp_path, cases)
    for (why, _entry, want), got in zip(FOLLOW_CASES, found):
        assert got == want, "%s: expected %r, got %r" % (why, want, got)


def test_auto_follow_survives_a_page_that_has_not_read_projects_yet(tmp_path):
    """The list arrives asynchronously; a turn can finish before it does."""
    entry = canned_job([tool_line("partforge_new_part: a small magnet holder")])
    for projects in (None, [], {"projects": []}):
        found = follow_choices(tmp_path, [{"job": entry, "projects": projects}])
        assert found == ["a-small-magnet-holder"], projects


def test_auto_follow_takes_the_projects_answer_in_any_shape_it_comes(tmp_path):
    """`/projects` itself, its list, or just the names — whichever is to hand."""
    entry = canned_job([tool_line("partforge_open_in_panel: part_base_ring.py")])
    whole = {"dir": "C:\\forge\\projects", "count": 3, "projects": KNOWN}
    found = follow_choices(tmp_path, [
        {"job": entry, "projects": whole},
        {"job": entry, "projects": KNOWN},
        {"job": canned_job([tool_line("partforge_new_part: cup")]),
         "projects": ["cup", "ring"]},
    ])
    assert found == ["ring", "ring", "cup"]


def test_auto_follow_never_throws_on_a_job_shape_it_has_not_seen(tmp_path):
    """A missing field must cost a missed switch, never a broken page."""
    cases = [{"job": shape, "projects": KNOWN} for shape in (
        None, {}, {"state": "done"}, {"state": "done", "activity": None},
        {"state": "done", "activity": [None, {}, {"kind": "tool"}]},
        {"state": "done", "reply": None, "message": None},
    )]
    assert follow_choices(tmp_path, cases) == ["", "", "", "", "", ""]


def test_every_destructive_button_asks_first_and_says_what_undoes_it(client):
    """Two buttons on this page destroy something, and they are not the same.

    Scrapping an object is a socket command with its own undo checkpoint, so it
    says which key puts it back.  Opening a project's ``.blend`` replaces the
    whole scene, and Blender resets the undo stack on a file load — so that one
    cannot promise a way back and does not pretend to: it names what would be
    lost instead.  The count is pinned so a third destructive button cannot be
    added without deciding which of those two it is.
    """
    html = fetch_text(client, "/")
    assert "Ctrl+Z in Blender puts it back" in html
    script = fetch_text(client, "/webui/app.js")
    assert script.count("window.confirm(") == 2
    assert "Undo does not cross a file load" in script


# ===========================================================================
# Phase 16 — the design phase: an SVG diagram in the chat, a sheet on the card
# ===========================================================================

#: A concept diagram exactly as the assistant writes one: boxes, a line, a
#: dimension callout.  Schematic, not art — which is also why it is 200 bytes.
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
       '<rect x="10" y="10" width="80" height="40" fill="none" stroke="#333"/>'
       '<text x="14" y="70">64 mm</text></svg>\n').encode("utf-8")


def write_svg(path, data=SVG):
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


def write_design(folder, name, data=None):
    """One document in a project's design/ folder."""
    directory = folder / "design"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if data is None:
        data = SVG if name.endswith(".svg") else b"# requirements\n\n1. 240 mm\n"
    path.write_bytes(data)
    return path


# -- the diagram has to be a picture, not a path -----------------------------

def test_an_svg_is_mintable_where_a_script_is_not(tmp_path):
    store = bridge.FileTokens(limit=4)
    diagram = write_svg(tmp_path / "concept.svg")
    token = store.mint(diagram)
    assert token and store.resolve(token) == os.path.abspath(diagram)


def test_find_file_paths_picks_a_concept_diagram_out_of_prose():
    found = bridge.find_file_paths(
        "The sheet is at C:\\forge\\projects\\ankle-fan\\design\\concept.svg "
        "- have a look before I build anything.")
    assert found == ["C:\\forge\\projects\\ankle-fan\\design\\concept.svg"]


def test_a_concept_diagram_named_in_a_reply_renders_as_an_image(bridges, tmp_path):
    """The whole point of adding .svg: the artist SEES the sketch.

    A design turn that hands back a path has handed back homework; one that
    hands back a picture has handed back something to sign off on.
    """
    diagram = write_svg(tmp_path / "concept.svg")
    client = bridges(env_extra={
        "FAKE_CLAUDE_REPLY": "Here is the concept sheet: %s" % diagram})
    job = client.turn("design me an ankle fan")

    assert len(job["files"]) == 1, job["files"]
    entry = job["files"][0]
    assert entry["path"] == os.path.abspath(diagram)
    # "image", so the page draws it inline instead of offering a download.
    assert entry["kind"] == "image"
    assert entry["ext"] == ".svg"

    status, headers, body = raw_get(client, entry["url"])
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    assert body == SVG


def test_an_svg_is_served_sandboxed(bridges, tmp_path):
    """Markup is the one servable type that can carry code.

    These diagrams are written by the assistant, never uploaded, so this is
    belt and braces rather than a hole being plugged - but a document served
    from this origin at its natural type is a document a browser would run
    scripts in, and one header costs nothing.
    """
    diagram = write_svg(tmp_path / "concept.svg")
    client = bridges(env_extra={"FAKE_CLAUDE_REPLY": "Sheet: %s" % diagram})
    job = client.turn("design it")
    _status, headers, _body = raw_get(client, job["files"][0]["url"])
    policy = headers["Content-Security-Policy"]
    assert "sandbox" in policy
    assert "default-src " + chr(39) + "none" + chr(39) in policy

    # ...and only there: a render is a bitmap and cannot run anything.
    png = write_png(tmp_path / "render.png")
    other = bridges(env_extra={"FAKE_CLAUDE_REPLY": "Render: %s" % png})
    shot = other.turn("render it")
    _status, png_headers, _body = raw_get(other, shot["files"][0]["url"])
    assert "Content-Security-Policy" not in png_headers


def test_an_svg_is_still_not_something_the_artist_can_attach():
    """Servable and attachable are different lists, on purpose.

    `.svg` travels out of a job into the conversation.  It does not travel in:
    Claude Code's Read tool renders bitmaps, so an SVG attachment would be a
    turn spent watching the model fail to look at it.
    """
    assert ".svg" not in bridge.IMAGE_EXTENSIONS
    assert ".svg" in bridge.DISPLAY_IMAGE_EXTENSIONS
    assert "Only images" in bridge.upload_error("concept.svg", SVG)
    assert "not an image" in bridge.image_error("C:\\forge\\concept.svg")


# -- the sheet on the card ---------------------------------------------------

def test_project_design_reads_the_sheet_in_reading_order(projects):
    """Not newest-first: what it has to do comes before what it looks like."""
    folder = projects("ankle-fan")
    for name in ("zeta.md", "components.md", "concept.svg", "requirements.md",
                 "alpha.md"):
        write_design(folder, name)
    (folder / "design" / "scratch").mkdir()
    found = bridge.project_design(str(folder))
    assert [item["file"] for item in found] == [
        "requirements.md", "concept.svg", "components.md", "alpha.md", "zeta.md"]
    assert all(item["size"] > 0 and item["mtime"] > 0 for item in found)
    assert found[1]["path"].endswith(os.path.join("design", "concept.svg"))


def test_project_design_of_a_project_that_skipped_the_phase_is_empty(projects):
    assert bridge.project_design(str(projects("cup"))) == []


def test_a_card_carries_the_design_sheet(bridges, projects):
    folder = projects("ankle-fan")
    write_design(folder, "requirements.md")
    write_design(folder, "concept.svg")
    client = bridges()

    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert [item["file"] for item in card["design"]] == ["requirements.md",
                                                         "concept.svg"]
    assert card["design_count"] == 2
    # It has a part.py, so it is past the gate.
    assert card["design_only"] is False


def test_a_project_that_is_only_a_design_sheet_is_still_on_the_shelf(bridges,
                                                                    tmp_path):
    """The state the sign-off gate holds: real work, no geometry.

    `project_entry` calls a folder with no script and no spec "somebody's
    notes", which is the right answer for the workbench picker and the wrong
    one here - a shelf that hid the sheet would hide the thing the artist is
    being asked to approve.
    """
    root = tmp_path / "projects"
    folder = root / "ankle-fan"
    folder.mkdir(parents=True)
    write_design(folder, "requirements.md")
    write_design(folder, "concept.svg")
    client = bridges()

    _status, body = client.request("/library", timeout=40)
    assert [p["name"] for p in body["projects"]] == ["ankle-fan"]
    card = body["projects"][0]
    assert card["design_only"] is True
    assert card["design_count"] == 2
    # Honest about what is not there yet, rather than inventing it.
    assert card["script"] == "" and card["script_path"] == ""
    assert card["has_params"] is False and card["param_count"] is None
    assert card["spec"] is None and card["components"] == []
    assert card["exports"] == [] and card["has_blend"] is False
    assert card["mtime"] > 0


def test_a_folder_with_neither_a_sheet_nor_a_part_is_still_not_a_project(bridges,
                                                                        projects):
    (projects.root / "notes").mkdir()
    (projects.root / "notes" / "todo.txt").write_text("later", encoding="utf-8")
    projects("cup")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    assert [p["name"] for p in body["projects"]] == ["cup"]


def test_saving_a_design_doc_moves_the_cards_clock(bridges, projects):
    """So the sheet somebody just revised sorts to the front of the shelf."""
    folder = projects("ankle-fan")
    old = time.time() - 4000
    for name in ("part.py", "spec.json"):
        os.utime(str(folder / name), (old, old))
    os.utime(str(folder), (old, old))
    write_design(folder, "requirements.md")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    assert body["projects"][0]["mtime"] > old + 1000


def test_the_card_draws_the_design_sheet_and_says_when_nothing_is_built(client):
    """The DOM half - the list, the fact, and the sentence for a bare sheet."""
    script = fetch_text(client, "/webui/app.js")
    assert "project.design" in script and "lib-design" in script
    assert "design doc" in script
    assert "project.design_only" in script
    assert "waiting for your sign-off" in script
    assert ".lib-design" in fetch_text(client, "/webui/app.css")


def test_the_reading_order_matches_the_mcp_servers(projects):
    """Two components, one order.  `forge_mcp.util.DESIGN_READING_ORDER` is the
    other copy, and a sheet that read differently on the card than in the tool
    report would be one of them quietly wrong."""
    assert bridge.DESIGN_READING_ORDER == (
        "requirements.md", "concept.svg", "mechanism.svg", "components.md")


def test_the_mechanism_diagram_reads_after_the_concept_one(projects):
    """Phase 17: the animated section is the still one with the motion in it."""
    folder = projects("litwick-lamp")
    for name in ("components.md", "mechanism.svg", "concept.svg",
                 "requirements.md"):
        write_design(folder, name)
    found = bridge.project_design(str(folder))
    assert [item["file"] for item in found] == [
        "requirements.md", "concept.svg", "mechanism.svg", "components.md"]


# ===========================================================================
# Phase 17 - mechanism demos: a film in the chat, a film on the card
# ===========================================================================

#: An .mp4 as far as anything here is concerned: the ISO base-media `ftyp` box
#: that Blender's ffmpeg writes, and enough bytes after it to be a file.  The
#: bridge never parses one - it serves the bytes - so the magic is here to keep
#: the fixture honest rather than because anything checks it.
MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
       + b"\x00\x00\x00\x08free" + b"\x00" * 64)


def write_mp4(path, data=MP4):
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


def write_demo(folder, name, data=MP4):
    """One rendered demo in a project's renders/ folder."""
    directory = folder / "renders"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


# -- the film has to be a film, not a path -----------------------------------

def test_an_mp4_is_mintable(tmp_path):
    store = bridge.FileTokens(limit=4)
    film = write_mp4(tmp_path / "demo-001-iso.mp4")
    token = store.mint(film)
    assert token and store.resolve(token) == os.path.abspath(film)


def test_find_file_paths_picks_a_demo_out_of_prose():
    found = bridge.find_file_paths(
        "The press cycle is at "
        "C:\\forge\\projects\\litwick-lamp\\renders\\demo-001-iso.mp4 - it is "
        "an illustration, not a simulation.")
    assert found == [
        "C:\\forge\\projects\\litwick-lamp\\renders\\demo-001-iso.mp4"]


def test_a_demo_named_in_a_reply_is_served_as_a_video(bridges, tmp_path):
    """The whole point of adding .mp4: the artist WATCHES the mechanism work."""
    film = write_mp4(tmp_path / "demo-001-iso.mp4")
    client = bridges(env_extra={
        "FAKE_CLAUDE_REPLY": "Here is the press cycle: %s" % film})
    job = client.turn("show me how the button works")

    assert len(job["files"]) == 1, job["files"]
    entry = job["files"][0]
    assert entry["path"] == os.path.abspath(film)
    # Its own class: a <video>, not an <img> and not a download chip.
    assert entry["kind"] == "video"
    assert entry["ext"] == ".mp4"

    status, headers, body = raw_get(client, entry["url"])
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert body == MP4


def test_a_video_is_not_an_image_and_never_an_attachment():
    """Display-only, the way `.svg` is - one medium further along.

    A film travels OUT of a job into the conversation.  It can never travel in:
    Claude Code's Read tool renders bitmaps, so an attached .mp4 would be a turn
    spent watching the model fail to look at it.
    """
    assert ".mp4" in bridge.SERVABLE_TYPES
    assert ".mp4" in bridge.DISPLAY_VIDEO_EXTENSIONS
    assert ".mp4" not in bridge.IMAGE_EXTENSIONS
    assert ".mp4" not in bridge.DISPLAY_IMAGE_EXTENSIONS
    assert "Only images" in bridge.upload_error("demo.mp4", MP4)
    assert "not an image" in bridge.image_error("C:\\forge\\demo.mp4")


def test_the_three_display_classes_are_distinct():
    assert bridge.file_kind("C:\\forge\\demo.mp4") == "video"
    assert bridge.file_kind("C:\\forge\\concept.svg") == "image"
    assert bridge.file_kind("C:\\forge\\render.png") == "image"
    assert bridge.file_kind("C:\\forge\\gecko.glb") == "model"


def test_a_video_is_not_served_with_the_svg_sandbox(bridges, tmp_path):
    """The CSP is about markup that could run; an .mp4 is bytes a decoder reads."""
    film = write_mp4(tmp_path / "demo-001-iso.mp4")
    client = bridges(env_extra={"FAKE_CLAUDE_REPLY": "Demo: %s" % film})
    job = client.turn("film it")
    _status, headers, _body = raw_get(client, job["files"][0]["url"])
    assert "Content-Security-Policy" not in headers
    assert headers["Content-Type"] == "video/mp4"


def test_a_demo_the_model_only_mentioned_is_not_minted(bridges, tmp_path):
    """Same rule as every other servable: a path that is not a file is not a
    token, so a demo that was never rendered cannot become a broken player."""
    missing = str(tmp_path / "never-rendered.mp4")
    client = bridges(env_extra={"FAKE_CLAUDE_REPLY": "It would go to %s" % missing})
    job = client.turn("film it")
    assert job["files"] == []


# -- the demo on the card ----------------------------------------------------

def test_project_demos_reads_renders_newest_first(projects):
    folder = projects("litwick-lamp")
    older = write_demo(folder, "demo-001-iso.mp4")
    newer = write_demo(folder, "demo-002-front.mp4")
    os.utime(str(older), (time.time() - 500, time.time() - 500))
    # A still render in the same folder is not a demo.
    write_png(str(folder / "renders" / "preview.png"))

    found = bridge.project_demos(str(folder))
    assert [item["file"] for item in found] == ["demo-002-front.mp4",
                                                "demo-001-iso.mp4"]
    assert found[0]["path"] == str(newer)
    assert all(item["size"] > 0 and item["mtime"] > 0 for item in found)


def test_project_demos_of_a_project_with_no_renders_folder_is_empty(projects):
    assert bridge.project_demos(str(projects("cup"))) == []


def test_a_card_carries_its_demos_as_playable_tokens(bridges, projects):
    """Paths, unlike the design sheet next door: a film nobody can press play
    on is a filename, which is the state this phase exists to leave behind."""
    folder = projects("litwick-lamp")
    write_demo(folder, "demo-001-iso.mp4")
    client = bridges()

    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert card["demo_count"] == 1
    demo = card["demos"][0]
    assert demo["file"] == "demo-001-iso.mp4"
    assert demo["url"] == "/file/%s" % demo["token"]

    status, headers, played = raw_get(client, demo["url"])
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert played == MP4


def test_a_card_lists_at_most_the_cap(bridges, projects):
    folder = projects("litwick-lamp")
    for index in range(bridge.MAX_DEMOS_LISTED + 3):
        write_demo(folder, "demo-%03d-iso.mp4" % index)
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert len(card["demos"]) == bridge.MAX_DEMOS_LISTED
    assert card["demo_count"] == bridge.MAX_DEMOS_LISTED


def test_a_project_with_no_demos_says_zero_rather_than_nothing(bridges, projects):
    projects("cup")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    card = body["projects"][0]
    assert card["demos"] == [] and card["demo_count"] == 0


def test_rendering_a_demo_moves_the_cards_clock(bridges, projects):
    """So the project somebody just filmed sorts to the front of the shelf."""
    folder = projects("litwick-lamp")
    old = time.time() - 4000
    for name in ("part.py", "spec.json"):
        os.utime(str(folder / name), (old, old))
    os.utime(str(folder), (old, old))
    write_demo(folder, "demo-001-iso.mp4")
    client = bridges()
    _status, body = client.request("/library", timeout=40)
    assert body["projects"][0]["mtime"] > old + 1000


def test_the_page_plays_a_video_inline_in_chat_and_on_the_card(client):
    """The DOM half: one <video> builder, used by the thread and by the shelf."""
    script = fetch_text(client, "/webui/app.js")
    assert "function demoVideo(" in script
    # Muted and looping on purpose: the clip is ~2 s and silent by construction.
    assert "video.loop = true" in script
    assert "video.muted = true" in script
    assert "video.controls = true" in script
    # Both surfaces reach for it.
    assert 'file.kind === "video"' in script
    assert "project.demos" in script and "lib-demos" in script
    css = fetch_text(client, "/webui/app.css")
    assert ".gallery video.demo" in css and ".lib-demos" in css
