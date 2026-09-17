"""End-to-end tests of the HTTP API against the reference sample.

These need the full dependency set (fastapi, httpx, build123d) and they spawn
the geometry worker subprocess, so they are the slow half of the suite.  Every
test that needs geometry is skipped -- not failed -- when build123d is missing,
so the parameter tests still tell you something on a bare checkout.
"""

from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service import main  # noqa: E402
from service import runner  # noqa: E402
from service.errors import ServiceError  # noqa: E402
from service.main import app  # noqa: E402

# Default geometry of samples/ring_band.py, for the assertions below.
RING_OUTER_DIAMETER_MM = 20.0
RING_HEIGHT_MM = 8.0

BAD_SCRIPT = '''
PARAMS = {"size": {"value": 10.0, "unit": "mm"}}

def build(p):
    raise ValueError("this part cannot be built")
'''

NO_PARAMS_SCRIPT = "def build(p):\n    return None\n"

#: A script that never returns.  The only containment against this is killing
#: the child process, which is the whole reason the worker is a subprocess.
HANGING_SCRIPT = '''
PARAMS = {"size": {"value": 10.0, "unit": "mm"}}

def build(p):
    while True:
        pass
'''


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def kernel(client):
    """Skip the module's geometry tests when build123d is not installed."""
    payload = client.get("/health").json()
    if payload.get("status") != "ok":
        pytest.skip(f"build123d unavailable: {payload.get('error')}")
    return payload


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------


def test_health_answers_200_with_a_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in ("ok", "degraded", "error")
    assert "build123d" in payload


def test_health_reports_a_build123d_version(client, kernel):
    assert kernel["build123d"]


# --------------------------------------------------------------------------
# /parse_params
# --------------------------------------------------------------------------


def test_parse_params_returns_the_resolved_schema(client, kernel, ring_band_source):
    response = client.post("/parse_params", json={"script": ring_band_source})
    assert response.status_code == 200, response.text

    schema = response.json()["params"]
    assert set(schema) >= {
        "outer_diameter",
        "height",
        "wall_thickness",
        "chamfer_edges",
        "chamfer_size",
    }
    assert schema["outer_diameter"]["value"] == RING_OUTER_DIAMETER_MM
    assert schema["outer_diameter"]["unit"] == "mm"
    assert schema["outer_diameter"]["min"] == 4.0
    assert schema["chamfer_edges"]["unit"] == "bool"
    assert schema["chamfer_edges"]["value"] is True


def test_parse_params_rejects_a_script_without_params(client):
    response = client.post("/parse_params", json={"script": NO_PARAMS_SCRIPT})
    assert response.status_code == 400
    body = response.json()
    assert "PARAMS" in body["error"]
    assert "traceback" in body


def test_parse_params_rejects_a_missing_script_field(client):
    response = client.post("/parse_params", json={})
    assert response.status_code == 400  # never FastAPI's default 422
    assert "error" in response.json()


# --------------------------------------------------------------------------
# /generate
# --------------------------------------------------------------------------


def test_generate_produces_a_watertight_mesh(client, kernel, ring_band_source):
    response = client.post(
        "/generate", json={"script": ring_band_source, "overrides": {}}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) >= {"params", "mesh", "stats"}

    mesh = body["mesh"]
    assert len(mesh["vertices"]) > 0
    assert len(mesh["faces"]) > 0
    assert all(len(face) == 3 for face in mesh["faces"])
    assert all(len(vertex) == 3 for vertex in mesh["vertices"])

    # Every index must be inside the vertex array.
    vertex_count = len(mesh["vertices"])
    assert max(max(face) for face in mesh["faces"]) < vertex_count
    assert min(min(face) for face in mesh["faces"]) >= 0

    stats = body["stats"]
    assert stats["vertex_count"] == vertex_count
    assert stats["face_count"] == len(mesh["faces"])
    assert stats["watertight"] is True, (
        f"ring band should be a closed solid; boundary_edges="
        f"{stats.get('boundary_edges')} nonmanifold={stats.get('nonmanifold_edges')}"
    )

    x, y, z = stats["bounding_box_mm"]
    assert x == pytest.approx(RING_OUTER_DIAMETER_MM, abs=0.5)
    assert y == pytest.approx(RING_OUTER_DIAMETER_MM, abs=0.5)
    assert z == pytest.approx(RING_HEIGHT_MM, abs=0.5)


def test_generate_applies_overrides(client, kernel, ring_band_source):
    response = client.post(
        "/generate",
        json={
            "script": ring_band_source,
            "overrides": {"height": 25.0, "outer_diameter": 40.0},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["params"]["height"]["value"] == 25.0
    x, _y, z = body["stats"]["bounding_box_mm"]
    assert z == pytest.approx(25.0, abs=0.5)
    assert x == pytest.approx(40.0, abs=0.5)


def test_generate_rejects_an_out_of_range_override(client, kernel, ring_band_source):
    response = client.post(
        "/generate",
        json={"script": ring_band_source, "overrides": {"wall_thickness": 0.01}},
    )
    assert response.status_code == 400
    body = response.json()
    assert "wall_thickness" in body["error"]
    assert "minimum" in body["error"]


def test_generate_rejects_an_unknown_override(client, kernel, ring_band_source):
    response = client.post(
        "/generate", json={"script": ring_band_source, "overrides": {"nope": 1}}
    )
    assert response.status_code == 400
    assert "nope" in response.json()["error"]


def test_generate_reports_a_script_exception_with_a_traceback(client, kernel):
    response = client.post("/generate", json={"script": BAD_SCRIPT})
    assert response.status_code == 400
    body = response.json()
    assert "this part cannot be built" in body["error"]
    assert body["traceback"] and "ValueError" in body["traceback"]


def test_generate_survives_a_failure_and_keeps_serving(client, kernel, ring_band_source):
    client.post("/generate", json={"script": BAD_SCRIPT})
    response = client.post("/generate", json={"script": ring_band_source})
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# /export
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["stl", "step", "3mf"])
def test_export_writes_a_file(client, kernel, ring_band_source, tmp_path, fmt):
    target = tmp_path / "sub dir" / f"ring_band.{fmt}"  # a space, on purpose
    response = client.post(
        "/export",
        json={
            "script": ring_band_source,
            "overrides": {"outer_diameter": 24.0},
            "format": fmt,
            "path": str(target),
        },
    )
    assert response.status_code == 200, response.text
    written = response.json()["path"]
    assert written
    assert target.exists()
    assert target.stat().st_size > 0


def test_export_rejects_a_relative_path(client, ring_band_source):
    response = client.post(
        "/export",
        json={
            "script": ring_band_source,
            "format": "stl",
            "path": "relative/out.stl",
        },
    )
    assert response.status_code == 400
    assert "absolute" in response.json()["error"]


def test_export_rejects_an_unknown_format(client, ring_band_source, tmp_path):
    response = client.post(
        "/export",
        json={
            "script": ring_band_source,
            "format": "obj",
            "path": str(tmp_path / "out.obj"),
        },
    )
    assert response.status_code == 400
    assert "format" in response.json()["error"]


# --------------------------------------------------------------------------
# Wire-format guard
# --------------------------------------------------------------------------


def test_generate_response_is_plain_json(client, kernel, ring_band_source):
    """The add-on parses this with stdlib json over urllib -- keep it boring."""
    response = client.post("/generate", json={"script": ring_band_source})
    assert response.headers["content-type"].startswith("application/json")
    json.loads(response.content.decode("utf-8"))


# --------------------------------------------------------------------------
# Windows specifics: paths with spaces, no console windows
# --------------------------------------------------------------------------


def test_export_handles_a_path_with_spaces_and_writes_a_real_binary_stl(
    client, kernel, ring_band_source, tmp_path
):
    """Windows is the primary platform: spaces in directory *and* file names."""
    target = tmp_path / "my parts" / "deeper dir" / "ring band v2.stl"
    response = client.post(
        "/export",
        json={"script": ring_band_source, "format": "stl", "path": str(target)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(target)
    assert target.exists()

    data = target.read_bytes()
    # Binary STL: 80-byte header, uint32 triangle count, 50 bytes per triangle.
    assert len(data) > 84
    (triangle_count,) = struct.unpack("<I", data[80:84])
    assert triangle_count > 0
    assert len(data) == 84 + 50 * triangle_count


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console-window guard")
def test_the_worker_is_spawned_without_a_console_window(monkeypatch):
    """Nothing this service does may flash a console on the user's desktop."""
    captured = {}

    def fake_popen(_command, **kwargs):
        captured.update(kwargs)
        raise OSError("not actually starting a process")

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    pool = runner.WorkerPool()
    with pytest.raises(ServiceError):
        pool._spawn()

    assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW


# --------------------------------------------------------------------------
# Worker lifecycle -- kept last: these kill the warm child on purpose.
# --------------------------------------------------------------------------


def test_a_hanging_script_is_killed_and_the_worker_recovers(
    client, kernel, ring_band_source
):
    """A runaway script must not wedge the service.

    ``kernel`` has already warmed the child, so the cold-start grace does not
    apply and the budget below is the real wall clock.
    """
    pool = runner.get_pool()
    assert pool._warm, "the worker should already be warm via /health"

    original_timeout = pool.timeout
    pool.timeout = 3.0
    try:
        response = client.post("/generate", json={"script": HANGING_SCRIPT})
    finally:
        pool.timeout = original_timeout

    assert response.status_code == 400, response.text
    assert "time limit" in response.json()["error"]

    # The child was killed, not left spinning.
    assert pool._process is None

    # ...and the next request transparently starts a fresh one.
    again = client.post("/generate", json={"script": ring_band_source})
    assert again.status_code == 200, again.text
    assert again.json()["stats"]["watertight"] is True


def test_the_service_recovers_when_the_worker_dies_underneath_it(
    client, kernel, ring_band_source
):
    """A crashed child (segfault, OOM kill) is replaced on the next request."""
    pool = runner.get_pool()
    assert pool._process is not None
    pool._process.kill()
    pool._process.wait(timeout=30)

    response = client.post("/generate", json={"script": ring_band_source})
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# B-6 -- a second start must not silently double up
#
# Every Forge service allows address reuse, so starting one twice does not
# fail: the loser of the port race stays resident.  The 2026-09-16 dogfood run
# found two of each service alive on the machine.  The guard asks the port
# whether a Forge service is already answering there, before anything binds.
# --------------------------------------------------------------------------


class _HealthHandler(BaseHTTPRequestHandler):
    """Answers /health the way the real service does, and nothing else."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        body = json.dumps(
            {"status": "ok", "build": {"sha": "06b6c99", "pid": 30404,
                                       "started": "2026-09-16T04:26:00Z",
                                       "uptime_s": 1602.0}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102 - keep the test output clean
        pass


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@pytest.fixture
def occupied_port():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_the_startup_guard_names_the_service_already_on_the_port(occupied_port):
    line = main.already_serving("127.0.0.1", occupied_port)
    assert "30404" in line, line
    assert "06b6c99" in line, line
    assert "FORGE_FORCE_START" in line, line


def test_the_startup_guard_treats_a_dead_port_as_free():
    assert main.already_serving("127.0.0.1", _free_port()) == ""


def test_run_exits_cleanly_instead_of_starting_a_second_service(
    occupied_port, monkeypatch, capsys
):
    """"Already running" is the state the caller wanted, so exit 0, not 1."""
    monkeypatch.delenv("FORGE_FORCE_START", raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("uvicorn must never be reached: one is running")

    monkeypatch.setattr("uvicorn.run", refuse)
    assert main.run(["--port", str(occupied_port)]) == 0
    assert "already answering" in capsys.readouterr().out


def test_force_start_skips_the_guard(occupied_port, monkeypatch):
    monkeypatch.setenv("FORGE_FORCE_START", "1")
    assert main.force_start() is True

    bound = {}

    def fake_run(_app, host=None, port=None, **kwargs):
        bound["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    assert main.run(["--port", str(occupied_port)]) == 0
    # It went straight past the guard and tried to bind the busy port anyway.
    assert bound["port"] == occupied_port
