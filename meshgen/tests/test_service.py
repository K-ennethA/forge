"""meshgen service tests - runnable with none of the ~15 GB of weights present.

Everything model-specific is behind a backend adapter, so the whole job API can
be exercised against the fake adapter in ``fake_backend.py``, injected through
``FORGE_MESHGEN_BACKEND_MODULES``.  The real-model run is a manual gate, not a
pytest: it needs a GPU and takes minutes.

What is covered here: config resolution, the /health envelope (including the
"models are missing, here is where to get them" guidance), the job lifecycle
shapes, option pass-through and validation, cancellation, and .glb stats.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

os.environ.setdefault("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")

from meshgen import config as config_module  # noqa: E402
from meshgen import glb, service  # noqa: E402
from meshgen.tests.fake_backend import write_triangle_glb  # noqa: E402

FAKE_ENV_KEYS = [
    "FORGE_MESHGEN_FAKE_MISSING", "FORGE_MESHGEN_FAKE_LOADED",
    "FORGE_MESHGEN_FAKE_FAIL", "FORGE_MESHGEN_FAKE_DELAY",
    "FORGE_MESHGEN_FAKE_MV",
]


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_fake_env():
    saved = {k: os.environ.get(k) for k in FAKE_ENV_KEYS}
    for key in FAKE_ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        os.environ.pop(key, None)
        if value is not None:
            os.environ[key] = value


@pytest.fixture
def config_file(tmp_path):
    """A config pointing every heavyweight path at an empty scratch tree."""
    models = tmp_path / "models"
    for folder in ("diffusion_models", "vae", "clip_vision",
                   "background_removal", "geometry_estimation"):
        (models / folder).mkdir(parents=True)
    data = {
        "comfyui_root": str(tmp_path / "comfyui"),
        "comfyui_python": str(tmp_path / "comfyui" / ".venv" / "Scripts" / "python.exe"),
        "models_root": str(models),
        "comfyui_output_dir": str(tmp_path / "out"),
        "comfyui_input_dir": str(tmp_path / "in"),
        "port": 0,
        "comfyui_port": 8188,
        "default_backend": "fake",
        "job_timeout_s": 60,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class Client:
    """Tiny HTTP helper bound to a running service."""

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        req = urllib.request.Request(self.base + path, data=body,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload=None):
        return self.request("POST", path, payload if payload is not None else {})

    def wait(self, job_id, want=("done", "error", "cancelled"), timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, job = self.get(f"/job/{job_id}")
            assert status == 200, job
            if job["state"] in want:
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} never reached {want}")


@pytest.fixture
def live(config_file, monkeypatch):
    """A real service on an ephemeral port, torn down after the test."""
    monkeypatch.setenv("FORGE_MESHGEN_CONFIG", str(config_file))
    monkeypatch.setenv("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")
    monkeypatch.setenv("FORGE_MESHGEN_BACKEND", "fake")

    server, app = service.build_server(config_module.load())
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(port), app
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "input.png"
    # A 1x1 PNG. Nothing reads the pixels; the service only checks it exists.
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000100ffff03000006"
        "0005574bd10000000049454e44ae426082"
    ))
    return path


# --------------------------------------------------------------------------
# config resolution
# --------------------------------------------------------------------------
def test_config_defaults_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_MESHGEN_CONFIG", str(tmp_path / "nope.json"))
    for key in list(os.environ):
        if key.startswith("FORGE_MESHGEN_") and key != "FORGE_MESHGEN_CONFIG":
            monkeypatch.delenv(key, raising=False)
    cfg = config_module.load()
    assert cfg.port == 8902
    assert cfg.comfyui_port == 8188
    assert cfg.default_backend == "trellis2"
    assert cfg.source is None


def test_config_file_wins_over_defaults(config_file, monkeypatch):
    monkeypatch.delenv("FORGE_MESHGEN_BACKEND", raising=False)
    cfg = config_module.load(config_file)
    assert cfg.default_backend == "fake"
    assert cfg.source == config_file
    assert cfg.models_dir.is_dir()


def test_env_overrides_win_over_the_file(config_file, monkeypatch):
    monkeypatch.setenv("FORGE_MESHGEN_BACKEND", "pixal3d")
    monkeypatch.setenv("FORGE_MESHGEN_PORT", "9999")
    cfg = config_module.load(config_file)
    assert cfg.default_backend == "pixal3d"
    assert cfg.port == 9999


def test_bad_env_override_is_rejected(config_file, monkeypatch):
    monkeypatch.setenv("FORGE_MESHGEN_PORT", "not-a-port")
    with pytest.raises(ValueError, match="FORGE_MESHGEN_PORT"):
        config_module.load(config_file)


def test_derived_paths_hang_off_one_root(config_file):
    cfg = config_module.load(config_file)
    assert cfg.model_path("vae", "x.safetensors").parent == cfg.models_dir / "vae"
    assert cfg.comfyui_main == cfg.comfyui_dir / "main.py"
    assert cfg.comfyui_url("/prompt").endswith(f":{cfg.comfyui_port}/prompt")


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------
def test_health_reports_the_default_backend_and_all_available(live):
    client, _ = live
    status, body = client.get("/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "meshgen"

    backend = body["backend"]
    for key in ("name", "model", "license", "loaded", "vram_gb"):
        assert key in backend, key
    assert backend["name"] == "fake"
    assert backend["loaded"] is False

    names = {b["name"] for b in body["available_backends"]}
    # the real adapters are always advertised, even with no weights on disk
    assert {"trellis2", "pixal3d", "fake", "fake-missing"} <= names


def test_health_says_what_is_missing_and_where_to_get_it(live):
    client, _ = live
    os.environ["FORGE_MESHGEN_FAKE_MISSING"] = "1"
    status, body = client.get("/health")
    assert status == 200
    assert body["status"] == "models_missing"

    missing = body["missing"]
    assert missing, "missing list must not be empty"
    entry = missing[0]
    assert "fake_weights.safetensors" in entry["what"]
    assert entry["path"].endswith("fake_weights.safetensors")
    assert entry["source"].startswith("http")
    # and the operator is told which file to edit to relocate the store
    assert "config.json" in body["hint"]


def test_health_lists_missing_weights_for_the_real_backends(live):
    """With an empty models dir, trellis2 must name every file and its URL."""
    client, _ = live
    _, body = client.get("/health")
    trellis = next(b for b in body["available_backends"] if b["name"] == "trellis2")
    assert trellis["ready"] is False
    wanted = {
        "trellis_2_int8_convrot.safetensors",
        "trellis_2_shape_vae_bf16.safetensors",
        "trellis_2_texture_vae_bf16.safetensors",
        "dino_v3_L_naf_fp32.safetensors",
        "birefnet.safetensors",
    }
    named = " ".join(item["what"] for item in trellis["missing"])
    for filename in wanted:
        assert filename in named, filename
    for item in trellis["missing"]:
        if "safetensors" in item["what"]:
            assert item["source"].startswith("https://huggingface.co/")
    assert trellis["license"].startswith("MIT")

    # MoGe belongs to Pixal3D alone: the template's switch node is lazy, so a
    # TRELLIS.2 run never loads it and must not demand the download.
    assert "moge" not in named.lower()
    pixal = next(b for b in body["available_backends"] if b["name"] == "pixal3d")
    pixal_named = " ".join(item["what"] for item in pixal["missing"])
    assert "moge_2_vitl_normal_fp16.safetensors" in pixal_named


def test_health_flags_an_unknown_default_backend(config_file, monkeypatch):
    monkeypatch.setenv("FORGE_MESHGEN_CONFIG", str(config_file))
    monkeypatch.setenv("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")
    monkeypatch.setenv("FORGE_MESHGEN_BACKEND", "no-such-backend")
    server, app = service.build_server(config_module.load())
    try:
        body = app.health()
        assert body["status"] == "misconfigured"
        assert body["backend"] is None
        assert "no-such-backend" in body["error"]
    finally:
        server.server_close()


# --------------------------------------------------------------------------
# the job lifecycle
# --------------------------------------------------------------------------
def test_generate3d_runs_a_job_to_done(live, image, tmp_path):
    client, _ = live
    out = tmp_path / "result.glb"
    status, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(out),
    })
    assert status == 202, body
    # the worker can pick the job up before the response is written, so the
    # state on the 202 is "queued or already past it" - never a finished state
    assert body["state"] in ("queued", "running")
    job_id = body["job_id"]
    assert job_id

    job = client.wait(job_id)
    assert job["state"] == "done", job
    assert job["mesh_path"] == str(out)
    assert out.is_file()
    assert job["stats"]["verts"] == 3
    assert job["stats"]["faces"] == 1
    assert job["progress"] == 1.0
    assert isinstance(job["duration_ms"], int)
    assert job["backend"] == "fake"


def test_default_output_path_sits_next_to_the_image(live, image):
    client, _ = live
    _, body = client.post("/generate3d", {"image_path": str(image)})
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    expected = image.with_name(image.stem + "_fake.glb")
    assert job["mesh_path"] == str(expected)
    assert expected.is_file()


def test_progress_is_reported_while_running(live, image, tmp_path):
    client, _ = live
    os.environ["FORGE_MESHGEN_FAKE_DELAY"] = "1.5"
    _, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(tmp_path / "p.glb"),
    })
    job_id = body["job_id"]

    seen_running = False
    deadline = time.time() + 15
    while time.time() < deadline:
        _, job = client.get(f"/job/{job_id}")
        if job["state"] == "running":
            seen_running = True
            if job["progress"] is not None:
                assert 0.0 <= job["progress"] <= 1.0
                assert job["stage"]
                break
        if job["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert seen_running, "job never reported the running state"
    client.wait(job_id)


def test_backend_can_be_chosen_per_request(live, image, tmp_path):
    client, _ = live
    _, body = client.post("/generate3d", {
        "image_path": str(image),
        "backend": "fake-missing",
        "output": str(tmp_path / "m.glb"),
    })
    job = client.wait(body["job_id"])
    assert job["state"] == "error"
    # ensure_ready() must name the file, the path it looked at, and the source
    assert "absent_model.safetensors" in job["error"]
    assert "expected at" in job["error"]
    assert "get it from" in job["error"]


def test_backend_failure_becomes_an_error_job(live, image, tmp_path):
    client, _ = live
    os.environ["FORGE_MESHGEN_FAKE_FAIL"] = "1"
    _, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(tmp_path / "f.glb"),
    })
    job = client.wait(body["job_id"])
    assert job["state"] == "error"
    assert "told to fail" in job["error"]


def test_options_reach_the_backend(live, image, tmp_path):
    client, app = live
    _, body = client.post("/generate3d", {
        "image_path": str(image),
        "output": str(tmp_path / "o.glb"),
        "options": {"seed": 7, "texture_resolution": 1024},
    })
    client.wait(body["job_id"])
    job = app.store.get(body["job_id"])
    assert job.result["options_seen"] == {"seed": 7, "texture_resolution": 1024}


def test_jobs_queue_rather_than_running_at_once(live, image, tmp_path):
    """One GPU, one job. The second must not start before the first finishes."""
    client, _ = live
    os.environ["FORGE_MESHGEN_FAKE_DELAY"] = "1.0"
    ids = []
    for i in range(2):
        _, body = client.post("/generate3d", {
            "image_path": str(image), "output": str(tmp_path / f"q{i}.glb"),
        })
        ids.append(body["job_id"])

    states = [client.get(f"/job/{i}")[1]["state"] for i in ids]
    assert states.count("running") <= 1, states
    for job_id in ids:
        assert client.wait(job_id, timeout=30)["state"] == "done"


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------
def test_cancel_stops_a_running_job(live, image, tmp_path):
    client, _ = live
    os.environ["FORGE_MESHGEN_FAKE_DELAY"] = "5"
    _, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(tmp_path / "c.glb"),
    })
    job_id = body["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/job/{job_id}")[1]["state"] == "running":
            break
        time.sleep(0.05)

    status, result = client.post(f"/cancel/{job_id}")
    assert status == 200
    assert result["cancelled"] is True

    job = client.wait(job_id, want=("cancelled", "done", "error"), timeout=20)
    assert job["state"] == "cancelled", job
    assert not (tmp_path / "c.glb").exists()


def test_cancelling_a_finished_job_is_honest_not_an_error(live, image, tmp_path):
    client, _ = live
    _, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(tmp_path / "d.glb"),
    })
    client.wait(body["job_id"])
    status, result = client.post(f"/cancel/{body['job_id']}")
    assert status == 200
    assert result["cancelled"] is False
    assert result["state"] == "done"


def test_cancel_unknown_job_is_404(live):
    client, _ = live
    status, _ = client.post("/cancel/does-not-exist")
    assert status == 404


# --------------------------------------------------------------------------
# request validation
# --------------------------------------------------------------------------
def test_missing_image_path_is_rejected(live):
    client, _ = live
    status, body = client.post("/generate3d", {})
    assert status == 400
    assert "image_path" in body["error"]


def test_relative_image_path_is_rejected(live):
    client, _ = live
    status, body = client.post("/generate3d", {"image_path": "relative.png"})
    assert status == 400
    assert "absolute" in body["error"]


def test_absent_image_is_rejected_before_queueing(live, tmp_path):
    client, _ = live
    status, body = client.post("/generate3d", {"image_path": str(tmp_path / "ghost.png")})
    assert status == 400
    assert "not found" in body["error"]


def test_unknown_backend_lists_the_real_ones(live, image):
    client, _ = live
    status, body = client.post("/generate3d", {
        "image_path": str(image), "backend": "stable-nonsense",
    })
    assert status == 400
    assert "stable-nonsense" in body["error"]
    assert "trellis2" in body["available_backends"]


def test_non_glb_output_is_rejected(live, image, tmp_path):
    client, _ = live
    status, body = client.post("/generate3d", {
        "image_path": str(image), "output": str(tmp_path / "thing.stl"),
    })
    assert status == 400
    assert ".glb" in body["error"]


def test_options_must_be_an_object(live, image):
    client, _ = live
    status, body = client.post("/generate3d", {"image_path": str(image), "options": [1, 2]})
    assert status == 400
    assert "options" in body["error"]


def test_unknown_job_is_404(live):
    client, _ = live
    status, _ = client.get("/job/nope")
    assert status == 404


def test_unknown_route_is_404(live):
    client, _ = live
    assert client.get("/wat")[0] == 404
    assert client.post("/wat")[0] == 404


def test_bad_json_body_is_a_clean_400(live):
    client, _ = live
    req = urllib.request.Request(client.base + "/generate3d", data=b"{not json",
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert "JSON" in json.loads(exc.read().decode())["error"]


# --------------------------------------------------------------------------
# the real adapters, without their weights
# --------------------------------------------------------------------------
def test_real_adapters_declare_their_licences_and_switch_flags(live):
    _, app = live
    trellis = app.backends["trellis2"]
    pixal = app.backends["pixal3d"]
    assert trellis.use_trellis2 is True
    assert pixal.use_trellis2 is False
    assert "MIT" in trellis.license and "MIT" in pixal.license
    assert trellis.diffusion_weight[1] == "trellis_2_int8_convrot.safetensors"
    assert pixal.diffusion_weight[1] == "pixal3d_int8_convrot.safetensors"


def test_workflow_template_has_the_nodes_the_adapters_patch():
    """Guards against a template refresh renumbering the nodes we reach into."""
    from meshgen.backends import comfyui_base as base

    path = Path(__file__).resolve().parent.parent / "workflows" / "image_to_3d.json"
    graph = json.loads(path.read_text(encoding="utf-8"))

    assert graph[base.NODE_LOAD_IMAGE]["class_type"] == "LoadImage"
    assert graph[base.NODE_TRELLIS2_SWITCH]["class_type"] == "PrimitiveBoolean"
    assert graph[base.NODE_SAVE]["class_type"] == "Save3DAdvanced"
    for node_ids, field, _ in base.OPTION_SPEC.values():
        # an option may steer more than one node - crease_angle owns both
        # MeshSmoothNormals nodes, see backends/comfyui_base.py
        for node_id in ((node_ids,) if isinstance(node_ids, str) else node_ids):
            assert node_id in graph, node_id
            assert field in graph[node_id]["inputs"], (node_id, field)

    # every node link must point at a node that survived the prune
    for node_id, node in graph.items():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in graph, f"{node_id} references missing node {value[0]}"


def test_build_graph_sets_image_switch_and_output(live):
    _, app = live
    trellis = app.backends["trellis2"]
    from meshgen.backends import comfyui_base as base

    graph = trellis.build_graph("pic.png", {"seed": 11, "texture_resolution": 512}, "forge/x")
    assert graph[base.NODE_LOAD_IMAGE]["inputs"]["image"] == "pic.png"
    assert graph[base.NODE_TRELLIS2_SWITCH]["inputs"]["value"] is True
    assert graph[base.NODE_SAVE]["inputs"]["filename_prefix"] == "forge/x"
    assert graph[base.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"] == 11
    assert graph[base.NODE_TEXTURE_RESOLUTION]["inputs"]["value"] == 512

    pixal_graph = app.backends["pixal3d"].build_graph("pic.png", {}, "forge/y")
    assert pixal_graph[base.NODE_TRELLIS2_SWITCH]["inputs"]["value"] is False


def test_vram_safe_defaults_are_applied_before_caller_options(live):
    """The stock template OOMs on a 12 GB card; meshgen must not ship that."""
    _, app = live
    from meshgen.backends import comfyui_base as base

    graph = app.backends["trellis2"].build_graph("pic.png", {}, "forge/x")
    assert graph[base.NODE_UPSAMPLE]["inputs"]["target_resolution"] == "1024"
    assert graph[base.NODE_REMESH]["inputs"]["resolution"] == 512
    assert graph[base.NODE_TEXTURE_RESOLUTION]["inputs"]["value"] == 2048
    assert graph[base.NODE_DECIMATE]["inputs"]["target_face_count"] == 200000

    # a caller with a bigger card can put the stock values back
    stock = app.backends["trellis2"].build_graph(
        "pic.png", {"shape_resolution": "1536", "remesh_resolution": 768}, "forge/x")
    assert stock[base.NODE_UPSAMPLE]["inputs"]["target_resolution"] == "1536"
    assert stock[base.NODE_REMESH]["inputs"]["resolution"] == 768


def test_unknown_option_is_refused_with_the_supported_list(live):
    _, app = live
    from meshgen.backends.base import BackendError

    with pytest.raises(BackendError) as exc:
        app.backends["trellis2"].build_graph("pic.png", {"wobble": 3}, "forge/x")
    assert "wobble" in str(exc.value)
    assert "texture_resolution" in str(exc.value)


def test_collect_output_handles_both_history_shapes(live, tmp_path):
    """Save3DAdvanced reports a bare relative path, not a {filename,...} dict.

    Parsing only the dict form loses the mesh at the very last step, after
    minutes of GPU work - which is exactly what happened on the first real run.
    """
    _, app = live
    out_dir = app.config.output_dir
    (out_dir / "forge").mkdir(parents=True, exist_ok=True)
    write_triangle_glb(out_dir / "forge" / "trellis2_00001.glb")
    write_triangle_glb(out_dir / "sub" / "dict_style.glb")

    # the shape Save3DAdvanced actually emits
    entry = {"outputs": {
        "202": {"text": ["Vertices: 8,418,804"]},
        "302": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]},
        "322": {"result": ["forge/trellis2_00001.glb", None, []]},
    }}
    found = app.client.find_mesh_outputs(entry)
    assert [p.name for p in found] == ["trellis2_00001.glb"]

    target = tmp_path / "collected.glb"
    result = app.client.collect_output(entry, target)
    assert target.is_file()
    assert result["mesh_path"] == str(target)

    # and the classic dict shape still works
    dict_entry = {"outputs": {"9": {"result": [
        {"filename": "dict_style.glb", "subfolder": "sub", "type": "output"}]}}}
    assert [p.name for p in app.client.find_mesh_outputs(dict_entry)] == ["dict_style.glb"]


def test_collect_output_says_so_when_nothing_was_produced(live, tmp_path):
    _, app = live
    from meshgen.backends.base import BackendError

    with pytest.raises(BackendError, match="no mesh file"):
        app.client.collect_output({"outputs": {"1": {"images": [
            {"filename": "x.png", "subfolder": "", "type": "output"}]}}}, tmp_path / "n.glb")


def test_node_labels_disambiguate_repeated_titles(live):
    """Four nodes are titled 'KSampler'; a bare title makes a job look frozen."""
    _, app = live
    graph = app.backends["trellis2"].build_graph("pic.png", {}, "forge/x")
    labels = app.client.node_labels(graph)

    ksamplers = [v for v in labels.values() if v.startswith("KSampler")]
    assert len(ksamplers) > 1, "expected several KSampler nodes"
    assert len(set(ksamplers)) == len(ksamplers), f"labels collide: {ksamplers}"
    assert all("#" in v for v in ksamplers)

    # a title that appears once keeps its plain name
    assert "Save3DAdvanced" in labels.values()


def test_comfyui_runtime_missing_is_reported_with_install_commands(live):
    _, app = live
    missing = app.client.runtime_missing()
    assert len(missing) == 2
    joined = json.dumps(missing)
    assert "git clone" in joined
    assert "download.pytorch.org/whl/cu130" in joined


# --------------------------------------------------------------------------
# .glb stats
# --------------------------------------------------------------------------
def test_glb_stats_counts_verts_and_faces(tmp_path):
    path = write_triangle_glb(tmp_path / "tri.glb")
    stats = glb.stats(path)
    assert stats["verts"] == 3
    assert stats["faces"] == 1
    assert stats["meshes"] == 1


def test_glb_stats_rejects_a_non_glb(tmp_path):
    path = tmp_path / "not.glb"
    path.write_bytes(b"this is not a glb at all")
    with pytest.raises(glb.GlbError):
        glb.stats(path)
