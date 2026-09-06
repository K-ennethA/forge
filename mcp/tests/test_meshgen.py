"""Phase 7: generate_3d / meshgen_status — a picture becomes a mesh.

Three fakes and no GPU:

* **meshgen** is a stdlib HTTP server on an ephemeral port — never 8902 — that
  scripts a job the way the real one does: queued, then running with a stage
  name that CHANGES, then done with stats, vram and a mesh path.
* **Blender** is the NDJSON fake from ``test_blender_client``, answering
  ``import_generated`` and ``check_model``.
* The geometry service is never contacted: on this path it is the add-on that
  posts to ``/check_mesh``.

What is pinned here is what a five-minute job must never get wrong — exactly
what goes on the wire, that the repair is not optional, that the stages the job
went through survive into the report, and that Blender being down loses the
import but never the .glb.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import BackendError, BackendUnavailable

from .conftest import REAL_BACKEND_PORTS
from .test_blender_client import FakeBlender
from .test_rigforge import router

# --- a fake meshgen ---------------------------------------------------------

#: The job states one generation walks through, in order. The stage name moving
#: on mid-run is the point: it is what the report has to keep.
SCRIPT = [
    {"state": "queued", "progress": None, "stage": None},
    {"state": "running", "progress": 0.3, "stage": "Trellis2UpsampleStage"},
    {"state": "running", "progress": 0.8, "stage": "Trellis2UpsampleStage"},
    {"state": "running", "progress": 0.2, "stage": "RemeshMesh"},
    {"state": "running", "progress": 0.5, "stage": "UnwrapMesh"},
    {
        "state": "done",
        "progress": 1.0,
        "stage": None,
        "mesh_path": r"C:\forge-models\comfyui-output\gecko_trellis2.glb",
        "stats": {"verts": 134216, "faces": 199694, "materials": 1},
        "duration_ms": 304000,
        "model": "TRELLIS.2 int8 convrot",
        "vram": {"before_gb": 0.4, "peak_gb": 8.15, "after_gb": 1.1, "total_gb": 11.94},
    },
]

HEALTH = {
    "status": "ok",
    "service": "meshgen",
    "version": "0.1.0",
    "port": 8902,
    "models_root": r"C:\forge-models\models",
    "comfyui_running": False,
    "backend": {
        "name": "trellis2",
        "model": "TRELLIS.2 int8 convrot",
        "license": "MIT",
        "loaded": False,
        "vram_gb": 9,
        "ready": True,
    },
    "available_backends": [
        {"name": "trellis2", "model": "TRELLIS.2 int8 convrot", "license": "MIT",
         "loaded": False, "vram_gb": 9, "ready": True, "missing": []},
        {"name": "pixal3d", "model": "Pixal3D int8 convrot", "license": "MIT",
         "loaded": False, "vram_gb": 10, "ready": True, "missing": []},
    ],
    "jobs": {"active": 0, "queued": 0},
}

MODELS_MISSING = dict(
    HEALTH,
    status="models_missing",
    missing=[{
        "what": "trellis_2_shape_vae_bf16.safetensors",
        "path": r"C:\forge-models\models\vae\trellis_2_shape_vae_bf16.safetensors",
        "source": "https://huggingface.co/Comfy-Org/TRELLIS.2",
        "bytes": 1020000000,
    }],
    hint="Each entry says what is missing, the exact path meshgen looks at, and "
         "the URL to fetch it from.",
)


class FakeMeshgen:
    """meshgen's four routes, scripted. Ephemeral port, never 8902."""

    def __init__(self, script=None, health=None):
        self.script = list(script if script is not None else SCRIPT)
        self.health_payload = dict(health if health is not None else HEALTH)
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.job_polls = 0
        self.job_id = "11111111-2222-3333-4444-555555555555"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0].rstrip("/") or "/"
                if path in ("/health", "/"):
                    return self._send(200, outer.health_payload)
                if path.startswith("/job/"):
                    wanted = path[len("/job/"):]
                    if wanted != outer.job_id:
                        return self._send(404, {"error": "no such job"})
                    index = min(outer.job_polls, len(outer.script) - 1)
                    outer.job_polls += 1
                    payload = dict(outer.script[index])
                    payload.update({"job_id": outer.job_id, "backend": "trellis2"})
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
                outer.posts.append((path, body))
                if path == "/generate3d":
                    if body.get("backend") not in (None, "", "trellis2", "pixal3d"):
                        return self._send(400, {
                            "error": "unknown backend %r" % body.get("backend"),
                            "available_backends": ["pixal3d", "trellis2"]})
                    return self._send(202, {
                        "job_id": outer.job_id, "state": "queued",
                        "backend": body.get("backend") or "trellis2",
                        "output": r"C:\forge-models\comfyui-output\gecko_trellis2.glb"})
                return self._send(404, {"error": "no route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        assert self.port not in REAL_BACKEND_PORTS
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def meshgen(monkeypatch):
    """Factory: start a fake meshgen and aim the client at it. No sleeping."""
    started: list[FakeMeshgen] = []

    def make(script=None, health=None) -> FakeMeshgen:
        fake = FakeMeshgen(script, health)
        started.append(fake)
        monkeypatch.setattr(config, "MESHGEN_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "MESHGEN_PORT", fake.port)
        monkeypatch.setattr(config, "MESHGEN_URL", f"http://127.0.0.1:{fake.port}")
        monkeypatch.setattr(config, "MESHGEN_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "MESHGEN_READ_TIMEOUT", 10.0)
        monkeypatch.setattr(config, "MESHGEN_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(config, "MESHGEN_JOB_TIMEOUT", 30.0)
        return fake

    yield make
    for fake in started:
        fake.close()


@pytest.fixture
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "gecko.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


# --- the add-on's answers ---------------------------------------------------

IMPORTED = {
    "object": "gecko_trellis2",
    "vertex_count": 41233,
    "face_count": 82312,
    "edge_count": 123456,
    "repaired": True,
    "path": r"C:\forge-models\comfyui-output\gecko_trellis2.glb",
    "importer": "gltf",
    "imported_objects": 2,
    "dimensions_mm": [812.4, 640.0, 999.9],
    "before": {"vertex_count": 134216, "face_count": 199694, "edge_count": 0},
    "voxel_size": 0.0045,
    "voxel_size_mm": 4.5,
    "repair_method": "operator",
}

CHECKED = {
    "object": "gecko_trellis2",
    "overall": "fail",
    "checks": [
        {"name": "bed_fit", "status": "pass", "details": "fits", "data": {}},
        {"name": "min_wall", "status": "fail",
         "details": "3586 of 3861 probes under the 0.8 mm minimum", "data": {}},
        {"name": "overhangs", "status": "warn", "details": "44975.9 mm2", "data": {}},
        {"name": "watertight", "status": "pass", "details": "closed", "data": {}},
    ],
    "mesh": {"vertex_count": 41233, "face_count": 82312, "scale": 1000.0},
    "printer_source": "service defaults",
    "panel": True,
}


@pytest.fixture
def blender(monkeypatch):
    started: list[FakeBlender] = []

    def make(results: Any, connections: int = 2) -> FakeBlender:
        fake = FakeBlender(router(results), connections=connections)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "BLENDER_PORT", fake.port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


# --- generate_3d: the wire --------------------------------------------------


def test_generate_3d_posts_the_absolute_image_path(meshgen, blender, picture: Path) -> None:
    fake = meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture))

    path, body = fake.posts[0]
    assert path == "/generate3d"
    assert body["image_path"] == str(picture)
    # No backend key at all: "omitted" must keep meaning "the service's default"
    # rather than an empty string it would have to interpret.
    assert "backend" not in body


def test_generate_3d_passes_a_named_backend_through(meshgen, blender, picture: Path) -> None:
    fake = meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture), backend="pixal3d")
    assert fake.posts[0][1]["backend"] == "pixal3d"


def test_an_unknown_backend_is_refused_with_the_list(meshgen, picture: Path) -> None:
    meshgen()
    with pytest.raises(BackendError) as excinfo:
        server.generate_3d(str(picture), backend="dreamfusion")
    assert "available backends" in str(excinfo.value)
    assert "trellis2" in str(excinfo.value)


@pytest.mark.parametrize("name", ["sketch.txt", "model.glb", "notes.pdf"])
def test_a_file_that_is_not_a_picture_never_reaches_the_service(
    meshgen, tmp_path: Path, name: str
) -> None:
    fake = meshgen()
    path = tmp_path / name
    path.write_bytes(b"x")
    with pytest.raises(Exception) as excinfo:
        server.generate_3d(str(path))
    assert ".png" in str(excinfo.value)
    assert fake.posts == []  # refused before a five-minute job was started


def test_a_missing_picture_is_a_path_problem(meshgen, tmp_path: Path) -> None:
    fake = meshgen()
    with pytest.raises(Exception) as excinfo:
        server.generate_3d(str(tmp_path / "nope.png"))
    assert "No file at" in str(excinfo.value)
    assert fake.posts == []


def test_the_import_asks_for_the_repair_every_time(meshgen, blender, picture: Path) -> None:
    """Raw image-to-3D output is never manifold: repair is not a preference."""
    meshgen()
    fake = blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture))

    sent = fake.requests[0]
    assert sent["type"] == "import_generated"
    assert sent["params"]["path"] == SCRIPT[-1]["mesh_path"]
    assert sent["params"]["repair"] is True


def test_the_object_is_named_after_the_picture(meshgen, blender, picture: Path) -> None:
    """A .glb calls its mesh `Mesh_0`, which tells the artist nothing."""
    meshgen()
    fake = blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture))
    assert fake.requests[0]["params"]["name"] == "gecko"


def test_the_verdict_comes_from_check_model_on_what_landed(
    meshgen, blender, picture: Path
) -> None:
    meshgen()
    fake = blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture))

    assert [r["type"] for r in fake.requests] == ["import_generated", "check_model"]
    assert fake.requests[1]["params"]["object"] == "gecko_trellis2"


def test_the_job_is_followed_to_the_end(meshgen, blender, picture: Path) -> None:
    fake = meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    server.generate_3d(str(picture))
    assert fake.job_polls == len(SCRIPT)  # every state, up to and including done


# --- generate_3d: the report ------------------------------------------------


def test_the_report_names_the_object_the_duration_and_the_stages(
    meshgen, blender, picture: Path
) -> None:
    meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    report = server.generate_3d(str(picture))

    assert "gecko.png" in report
    assert "'gecko_trellis2'" in report
    assert "5 min 04 s" in report
    # The stage names are the honest account of those five minutes, in order and
    # without the repeat.
    assert "Trellis2UpsampleStage -> RemeshMesh -> UnwrapMesh" in report
    assert "progress per stage, never for the whole job" in report
    assert "trellis2" in report and "TRELLIS.2 int8 convrot" in report
    assert "peak VRAM 8.15 GB of 11.94" in report


def test_the_report_says_the_mesh_was_repaired_and_what_it_was_before(
    meshgen, blender, picture: Path
) -> None:
    meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    report = server.generate_3d(str(picture))

    assert "voxel-repaired at 4.5 mm" in report
    assert "199694 faces" in report  # the raw output, before the repair
    assert "mandatory, not a preference" in report


def test_the_report_carries_the_print_verdict_without_dressing_it_up(
    meshgen, blender, picture: Path
) -> None:
    meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    report = server.generate_3d(str(picture))

    assert "print verdict: FAIL" in report
    assert "[FAIL] min_wall" in report
    assert "[PASS] watertight" in report
    assert "usual for a generated mesh" in report


def test_the_report_refuses_to_promise_precision(meshgen, blender, picture: Path) -> None:
    """The one thing a generated mesh must never be sold as."""
    meshgen()
    blender({"import_generated": IMPORTED, "check_model": CHECKED})
    report = server.generate_3d(str(picture))

    assert "no crisp flat faces, no exact dimensions and no fine detail" in report
    assert "rigforge_retopo" in report  # the game-asset next step
    assert "check_model" in report      # the printing next step
    assert "812.4 x 640 x 999.9 mm" in report
    assert "scale it to whatever the artist tells you" in report


def test_blender_being_down_loses_the_import_but_never_the_file(
    meshgen, picture: Path, dead_backends
) -> None:
    """Minutes of GPU time produced a file: report the path, not a bare failure."""
    fake = meshgen()
    report = server.generate_3d(str(picture))

    assert SCRIPT[-1]["mesh_path"] in report
    assert "Blender is not running" in report
    assert "import_generated" in report
    assert "safe" in report
    assert fake.job_polls == len(SCRIPT)  # the job still ran to the end


def test_a_failed_job_says_what_the_service_said(meshgen, picture: Path) -> None:
    meshgen(script=[{"state": "error", "error": "torch.OutOfMemoryError: 20 GiB"}])
    with pytest.raises(BackendError) as excinfo:
        server.generate_3d(str(picture))
    message = str(excinfo.value)
    assert "torch.OutOfMemoryError" in message
    assert "Nothing is in the scene" in message


def test_a_job_that_outlasts_the_budget_is_handed_back_not_declared_dead(
    meshgen, monkeypatch, picture: Path
) -> None:
    meshgen(script=[{"state": "running", "stage": "RemeshMesh", "progress": 0.4}])
    monkeypatch.setattr(config, "MESHGEN_JOB_TIMEOUT", 0.0)
    report = server.generate_3d(str(picture))

    assert "did not finish" in report
    assert "meshgen_status(job_id=" in report
    assert "nothing was lost" in report


def test_wait_false_hands_back_the_job_id_without_polling(
    meshgen, picture: Path
) -> None:
    fake = meshgen()
    report = server.generate_3d(str(picture), wait=False)

    assert fake.job_polls == 0
    assert fake.job_id in report
    assert "meshgen_status" in report
    assert "about five" in report
    assert "import_generated" in report


def test_meshgen_being_down_is_one_sentence_not_a_retry(picture: Path, dead_backends) -> None:
    with pytest.raises(BackendUnavailable) as excinfo:
        server.generate_3d(str(picture))
    message = str(excinfo.value)
    assert "not running" in message
    assert "Start services" in message
    assert "do not retry blindly" in message


# --- meshgen_status ---------------------------------------------------------


def test_status_reports_the_backend_and_the_queue(meshgen) -> None:
    meshgen()
    report = server.meshgen_status()

    assert "[UP]" in report
    assert "trellis2 (TRELLIS.2 int8 convrot)" in report
    assert "licence MIT" in report
    assert "also installed: pixal3d" in report
    assert "one at a time" in report
    assert "starts on the first job" in report


def test_status_names_every_missing_model_file_and_where_it_goes(meshgen) -> None:
    meshgen(health=MODELS_MISSING)
    report = server.meshgen_status()

    assert "models_missing" in report
    assert "trellis_2_shape_vae_bf16.safetensors" in report
    assert r"C:\forge-models\models\vae" in report
    assert "huggingface.co" in report
    assert "nothing downloads on its own" in report


def test_status_reads_a_running_job_as_a_stage_not_a_percentage(meshgen) -> None:
    fake = meshgen()
    fake.job_polls = 3  # mid-run: RemeshMesh at 20%
    report = server.meshgen_status(fake.job_id)

    assert "state: running" in report
    assert "stage: RemeshMesh" in report
    assert "20% through THAT stage" in report
    assert "which stage it is on, not a percentage" in report


def test_status_of_a_finished_job_says_what_to_do_with_it(meshgen) -> None:
    fake = meshgen()
    fake.job_polls = len(SCRIPT) - 1
    report = server.meshgen_status(fake.job_id)

    assert "state: done" in report
    assert "gecko_trellis2.glb" in report
    assert "199694 faces" in report
    assert "import_generated" in report
    assert "voxel-repaired" in report


def test_status_of_an_unknown_job_says_so_without_failing(meshgen) -> None:
    meshgen()
    report = server.meshgen_status("not-a-job")
    assert "[UP]" in report
    assert "not-a-job" in report


def test_status_never_fails_when_the_service_is_down(dead_backends) -> None:
    report = server.meshgen_status()
    assert "[DOWN]" in report
    assert "optional" in report
    assert "18.5 GB" in report
    assert "parametric" in report


# --- the formatters, without any server at all ------------------------------


def test_a_thin_job_renders_without_exploding() -> None:
    report = util.fmt_generate_report(
        Path("sketch.png"), {"job_id": "j1"}, {"state": "done"}, []
    )
    assert "Generated a 3D shape from sketch.png" in report
    assert "Nothing is in the scene" in report


def test_an_unrepaired_import_says_so_in_capitals() -> None:
    report = util.fmt_generate_report(
        Path("sketch.png"), {}, {"state": "done", "mesh_path": "x.glb"}, [],
        imported=dict(IMPORTED, repaired=False),
    )
    assert "NOT repaired" in report


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 s"), (42.4, "42 s"), (60, "1 min 00 s"), (304, "5 min 04 s"),
     (None, "unknown")],
)
def test_durations_read_in_minutes(seconds: Any, expected: str) -> None:
    assert util.fmt_duration(seconds) == expected


def test_the_stage_trail_collapses_a_long_run() -> None:
    trail = util.fmt_stage_trail([f"Stage{i}" for i in range(20)])
    assert trail.startswith("Stage0 -> Stage1")
    assert "9 more" in trail
