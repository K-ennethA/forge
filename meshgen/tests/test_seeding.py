"""Seeds, the unscored candidate fan-out, backend choice and /capabilities.

The defect this suite pins (docs/research/3d-generation-and-detailing.md, Step 0):
an absent seed meant the template's fixed 56 on the structure sampler and a fixed
42/42/43 on the three tail samplers, so twelve "regenerations" of one picture
were one sample, byte for byte.  Now:

* ``seed`` reaches EVERY KSampler in both templates (``SEED_STAGES``), with the
  template's own spacing, so ``seed: 56`` still builds the template's graph;
* an absent seed is drawn and recorded - in the 202, the job and the result;
* the same request with the same seed builds the same graph submission.  That
  the GPU then returns the same mesh is NOT claimed here: it is measured for the
  structure stage only (README, seed ensemble) and is a later GPU replay run's question
  for the rest - an ASSUMPTION until then;
* ``/generate_ensemble`` fans N seeds out to N full generations and returns every
  candidate unranked - no scorer is imported, the caller picks.

ComfyUI is replaced by a recording client: no GPU, no ComfyUI, no weights.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")

from meshgen import config as config_module  # noqa: E402
from meshgen import jobs as jobs_module  # noqa: E402
from meshgen.backends import base as bbase  # noqa: E402
from meshgen.backends import comfyui_base as cbase  # noqa: E402
from meshgen.backends import multiview  # noqa: E402
from meshgen.backends.base import BackendError, Cancelled  # noqa: E402
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend  # noqa: E402
from meshgen.backends.comfyui_trellis2 import Trellis2Backend  # noqa: E402
from meshgen.comfyui_client import ComfyUIClient  # noqa: E402
from meshgen.tests.fake_backend import write_triangle_glb  # noqa: E402
from meshgen.tests.test_service import (Client, config_file, image,  # noqa: E402,F401
                                        live)

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"
SAMPLER_NODES = [node_id for _stage, node_id, _off in cbase.SEED_STAGES]


def _template(name):
    return json.loads((WORKFLOWS / name).read_text(encoding="utf-8"))


def _sampler_seeds(graph):
    return {node_id: graph[node_id]["inputs"]["seed"]
            for node_id in SAMPLER_NODES if node_id in graph}


# --------------------------------------------------------------------------
# a ComfyUI stand-in that records what would have been submitted
# --------------------------------------------------------------------------
class RecordingClient:
    def __init__(self, tmp_path, fail_seeds=(), cancel_event=None, cancel_after=None):
        self.tmp = Path(tmp_path)
        self.graphs = []
        self.staged = []
        self.unstaged = []
        self.fail_seeds = set(fail_seeds)
        self.cancel_event = cancel_event
        self.cancel_after = cancel_after
        self.started = 0

    def ensure_running(self, cancel_event=None):
        self.started += 1

    def is_running(self):
        return False

    def runtime_missing(self):
        return []

    def vram_report(self):
        return {"vram_used_gb": 1.0, "vram_total_gb": 12.0, "name": "fake-gpu"}

    def stage_image(self, path):
        name = f"staged_{len(self.staged)}.png"
        self.staged.append((str(path), name))
        return name

    def unstage_image(self, name):
        self.unstaged.append(name)

    def run_graph(self, graph, prompt_id, cancel_event=None, progress=None,
                  timeout_s=None, telemetry=None):
        self.graphs.append(copy.deepcopy(graph))
        seed = graph[cbase.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"]
        if progress:
            progress(0.5, "KSampler")
        if telemetry is not None:
            telemetry["peak_vram_gb"] = 8.0 + len(self.graphs) / 10
        if self.cancel_after is not None and len(self.graphs) >= self.cancel_after:
            self.cancel_event.set()
            raise Cancelled("cancelled")
        if seed in self.fail_seeds:
            raise BackendError(f"ComfyUI error at seed {seed}")
        return {"seed": seed}

    def collect_output(self, entry, out_path):
        produced = self.tmp / "comfy_out" / f"mesh_{entry['seed']}.glb"
        write_triangle_glb(produced)
        write_triangle_glb(out_path)
        return {"mesh_path": str(out_path), "comfyui_path": str(produced)}


def _backend(cls, client):
    backend = cls(config_module.load(), client)
    backend.ensure_ready = lambda: {"ready": True, "missing": []}
    return backend


@pytest.fixture
def trellis():
    config = config_module.load()
    return Trellis2Backend(config, ComfyUIClient(config))


# ==========================================================================
# 1. the seed reaches every sampler
# ==========================================================================
@pytest.mark.parametrize("template", ["image_to_3d.json", "multiview_to_3d.json"])
def test_seed_56_rebuilds_the_templates_own_sampler_seeds(template):
    """Continuity pin: every number the README measured at seed 56 was measured
    with samplers at 56/42/42/43.  The offsets must reproduce exactly that."""
    graph = _template(template)
    for stage, node_id, offset in cbase.SEED_STAGES:
        assert graph[node_id]["class_type"] == "KSampler", (template, stage)
        assert graph[node_id]["inputs"]["seed"] == 56 + offset, (template, stage)
    samplers = {k for k, v in graph.items() if v.get("class_type") == "KSampler"}
    assert samplers == set(SAMPLER_NODES), "a KSampler the seed table does not cover"


def test_seed_56_builds_a_graph_identical_to_no_seed(trellis):
    assert (trellis.build_graph("p.png", {"seed": 56}, "forge/x")
            == trellis.build_graph("p.png", {}, "forge/x"))


def test_the_seed_moves_all_four_samplers(trellis):
    graph = trellis.build_graph("p.png", {"seed": 1000}, "forge/x")
    assert _sampler_seeds(graph) == {"3": 1000, "18": 986, "23": 986, "12": 987}


def test_the_seed_reaches_the_multi_view_graph_too(tmp_path):
    pixal = Pixal3DBackend(config_module.load(), RecordingClient(tmp_path))
    paths = {}
    for name in ("front", "side"):
        path = tmp_path / f"{name}.png"
        path.write_bytes(b"x")
        paths[name] = str(path)
    plan = multiview.stage_plan(multiview.normalise_views(paths))
    graph = pixal.build_multiview_graph({"front": "a.png", "side": "b.png"}, plan,
                                        {"seed": 777}, "forge/mv")
    assert _sampler_seeds(graph) == {node: cbase.stage_seeds(777)[stage]
                                     for stage, node, _o in cbase.SEED_STAGES}


def test_same_request_same_seed_is_the_same_graph_submission(trellis):
    options = {"seed": 123456789, "texture_resolution": 1024, "hard_surface": True}
    first = trellis.build_graph("p.png", dict(options), "forge/x")
    second = trellis.build_graph("p.png", dict(options), "forge/x")
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_different_seed_changes_only_the_sampler_seeds(trellis):
    a = trellis.build_graph("p.png", {"seed": 10}, "forge/x")
    b = trellis.build_graph("p.png", {"seed": 11}, "forge/x")
    differing = {k for k in a if a[k] != b[k]}
    assert differing == set(SAMPLER_NODES)
    for node_id in SAMPLER_NODES:
        rest_a = {k: v for k, v in a[node_id]["inputs"].items() if k != "seed"}
        rest_b = {k: v for k, v in b[node_id]["inputs"].items() if k != "seed"}
        assert rest_a == rest_b


def test_texture_seed_rerolls_the_texture_alone(trellis):
    """Structure-locked re-roll: same shape seeds, a new texture draw."""
    base = trellis.build_graph("p.png", {"seed": 500}, "forge/x")
    reroll = trellis.build_graph("p.png", {"seed": 500, "texture_seed": 9}, "forge/x")
    assert reroll[cbase.NODE_TEXTURE_SAMPLER]["inputs"]["seed"] == 9
    for node_id in ("3", "18", "23"):
        assert reroll[node_id] == base[node_id]


def test_a_seed_near_zero_wraps_inside_the_samplers_range():
    seeds = cbase.stage_seeds(5)
    assert seeds["structure"] == 5
    assert seeds["shape"] == 2 ** 64 - 9
    assert all(0 <= s <= bbase.SEED_MAX for s in seeds.values())


@pytest.mark.parametrize("bad", [True, 1.5, -1, 2 ** 64, "abc", [1]])
def test_a_bad_seed_is_refused_not_coerced(trellis, bad):
    with pytest.raises(BackendError) as exc:
        trellis.build_graph("p.png", {"seed": bad}, "forge/x")
    assert "seed" in str(exc.value)
    with pytest.raises(BackendError):
        bbase.resolve_seed({"seed": bad})


def test_a_decimal_string_seed_is_the_same_seed():
    """64-bit seeds arrive as strings from clients that cannot hold them."""
    assert bbase.coerce_seed("18446744073709551615") == bbase.SEED_MAX
    assert bbase.coerce_seed("42") == 42


# ==========================================================================
# 2. an absent seed is drawn, and recorded everywhere
# ==========================================================================
def test_an_absent_seed_is_drawn_and_labelled():
    sent = {"texture_resolution": 1024}
    options, source = bbase.resolve_seed(sent)
    assert source == bbase.SEED_DRAWN
    assert 0 <= options["seed"] < bbase.DRAWN_SEED_BOUND
    assert sent == {"texture_resolution": 1024}, "the caller's dict was mutated"
    assert bbase.resolve_seed({"seed": None})[1] == bbase.SEED_DRAWN


def test_a_given_seed_is_kept_and_labelled_caller():
    options, source = bbase.resolve_seed({"seed": 7})
    assert (options["seed"], source) == (7, bbase.SEED_CALLER)


def test_draws_are_not_one_sample():
    """The measured defect: twelve regenerations that were one mesh."""
    assert len({bbase.draw_seed() for _ in range(12)}) > 1


def test_generate_records_the_seed_it_submitted(tmp_path):
    client = RecordingClient(tmp_path)
    backend = _backend(Trellis2Backend, client)
    result = backend.generate(str(tmp_path / "p.png"), {}, str(tmp_path / "o.glb"))
    seed = result["seed"]
    assert seed["source"] == "drawn"
    assert seed["value"] == seed["base"] == result["options"]["seed"]
    assert client.graphs[-1][cbase.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"] == seed["value"]
    assert seed["stages"] == cbase.stage_seeds(seed["value"])
    assert _sampler_seeds(client.graphs[-1]) == {
        node: seed["stages"][stage] for stage, node, _o in cbase.SEED_STAGES}


def test_generate_twice_at_one_seed_submits_identical_graphs(tmp_path):
    client = RecordingClient(tmp_path)
    backend = _backend(Trellis2Backend, client)
    for _ in range(2):
        result = backend.generate(str(tmp_path / "p.png"), {"seed": 31337},
                                  str(tmp_path / "o.glb"))
        assert result["seed"]["source"] == "caller"
    first, second = client.graphs
    # the staged filename differs per job by design (fresh stage each run);
    # everything the sampler sees must not
    first[cbase.NODE_LOAD_IMAGE]["inputs"]["image"] = "x"
    second[cbase.NODE_LOAD_IMAGE]["inputs"]["image"] = "x"
    assert first == second


def test_the_service_draws_before_queueing_and_the_record_carries_it(live, image):
    client, app = live
    status, body = client.post("/generate3d", {"image_path": str(image)})
    assert status == 202, body
    assert body["seed"]["source"] == "drawn"
    drawn = body["seed"]["value"]
    assert isinstance(drawn, int)
    job = client.wait(body["job_id"])
    assert job["state"] == "done"
    assert job["seed"] == {"value": drawn, "source": "drawn"}
    assert app.store.get(body["job_id"]).result["options_seen"]["seed"] == drawn


def test_two_identical_requests_without_a_seed_are_two_samples(live, image):
    client, _app = live
    seeds = []
    for _ in range(3):
        status, body = client.post("/generate3d", {"image_path": str(image)})
        assert status == 202, body
        seeds.append(body["seed"]["value"])
    assert len(set(seeds)) > 1


def test_a_callers_seed_is_echoed_as_caller(live, image):
    client, _app = live
    status, body = client.post("/generate3d", {"image_path": str(image),
                                               "options": {"seed": 99}})
    assert status == 202, body
    assert body["seed"] == {"value": 99, "source": "caller"}


def test_a_bad_seed_is_a_400_before_anything_is_queued(live, image):
    client, app = live
    status, body = client.post("/generate3d", {"image_path": str(image),
                                               "options": {"seed": -3}})
    assert status == 400, body
    assert "seed" in body["error"]
    assert app.queue.qsize() == 0 and not app.store.all()


def test_the_job_record_merges_the_result_seed_under_the_services_source():
    job = jobs_module.Job("id", "p.png", "trellis2", {"seed": 56}, "o.glb")
    job.seed_source = "drawn"
    assert job.as_dict()["seed"] == {"value": 56, "source": "drawn"}
    job.state = jobs_module.DONE
    job.started = job.finished = 1.0
    job.result = {"mesh_path": "o.glb", "options": {"seed": 58},
                  "seed": {"value": 58, "base": 56, "source": "caller",
                           "stages": cbase.stage_seeds(58)}}
    record = job.as_dict()
    # the backend saw a concrete seed and could only call it "caller"; the
    # service drew it, and the service's word is the one that is true
    assert record["seed"]["source"] == "drawn"
    assert record["seed"]["value"] == 58 and record["seed"]["base"] == 56
    assert record["options"] == {"seed": 58}


# ==========================================================================
# 3. the unscored fan-out
# ==========================================================================
def test_n_candidates_are_the_base_seed_and_its_successors():
    assert bbase.candidate_seeds({"seed": 40}, n=3) == [40, 41, 42]


def test_an_explicit_seed_list_is_used_as_given():
    assert bbase.candidate_seeds({}, seeds=[9, 3, 7]) == [9, 3, 7]


@pytest.mark.parametrize("kwargs,options,needle", [
    ({}, {"seed": 1}, "required"),
    ({"n": 0}, {"seed": 1}, "1 to 9"),
    ({"n": 10}, {"seed": 1}, "1 to 9"),
    ({"n": True}, {"seed": 1}, "integer"),
    ({"n": "3"}, {"seed": 1}, "integer"),
    ({"seeds": [1, 1]}, {}, "repeat"),
    ({"seeds": []}, {}, "non-empty"),
    ({"seeds": list(range(10))}, {}, "1 to 9"),
    ({"seeds": [1, 2]}, {"seed": 5}, "one or the other"),
    ({"seeds": [1, 2], "n": 3}, {}, "disagrees"),
    ({"seeds": [1, "x"]}, {}, "not an integer"),
    ({"n": 2}, {"seed": bbase.SEED_MAX}, "runs past"),
])
def test_a_malformed_fan_out_is_refused_never_clamped(kwargs, options, needle):
    with pytest.raises(BackendError) as exc:
        bbase.candidate_seeds(options, **kwargs)
    assert needle in str(exc.value)


def test_the_fan_out_submits_one_graph_per_seed_and_ranks_nothing(tmp_path, monkeypatch):
    client = RecordingClient(tmp_path)
    backend = _backend(Trellis2Backend, client)

    def no_scorer():
        raise AssertionError("the unscored fan-out must never load the scorer")
    monkeypatch.setattr(backend, "load_scorer", no_scorer)

    out = tmp_path / "part_trellis2.glb"
    result = backend.generate_candidates(str(tmp_path / "p.png"), {"seed": 70},
                                         str(out), [70, 71, 72])
    assert [g[cbase.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"] for g in client.graphs] \
        == [70, 71, 72]
    for graph, seed in zip(client.graphs, [70, 71, 72]):
        assert _sampler_seeds(graph) == {
            node: cbase.stage_seeds(seed)[stage] for stage, node, _o in cbase.SEED_STAGES}
    # staged once, unstaged once
    assert len(client.staged) == 1 and client.unstaged == [client.staged[0][1]]

    assert result["mesh_path"] is None
    assert not out.exists(), "an unscored fan-out wrote a 'winner' it never picked"
    ens = result["ensemble"]
    assert ens["tier"] == "candidates" and ens["scored"] is False
    assert ens["winner"] is None and ens["seeds"] == [70, 71, 72]
    cands = result["candidates"]
    assert [c["seed"] for c in cands] == [70, 71, 72]
    assert [c["index"] for c in cands] == [1, 2, 3]
    for c in cands:
        assert Path(c["mesh_path"]).name == f"part_trellis2_seed{c['seed']}.glb"
        assert Path(c["mesh_path"]).is_file()
        assert isinstance(c["duration_ms"], int) and c["duration_ms"] >= 0
        assert c["stats"]["faces"] == 1
        assert c["stage_seeds"] == cbase.stage_seeds(c["seed"])
        assert c["peak_vram_gb"] is not None
    assert ens["kept"] == [c["mesh_path"] for c in cands]
    assert result["vram"]["peak_gb"] == max(c["peak_vram_gb"] for c in cands)


def test_the_same_fan_out_twice_submits_the_same_graphs(tmp_path):
    graphs = []
    for run in range(2):
        client = RecordingClient(tmp_path / str(run))
        backend = _backend(Trellis2Backend, client)
        backend.generate_candidates(str(tmp_path / "p.png"), {"seed": 5},
                                    str(tmp_path / f"o{run}.glb"), [5, 6])
        graphs.append(client.graphs)
    assert graphs[0] == graphs[1]


def test_a_failed_candidate_is_recorded_and_the_rest_are_kept(tmp_path):
    client = RecordingClient(tmp_path, fail_seeds={11})
    backend = _backend(Trellis2Backend, client)
    result = backend.generate_candidates(str(tmp_path / "p.png"), {},
                                         str(tmp_path / "o.glb"), [10, 11, 12])
    by_seed = {c["seed"]: c for c in result["candidates"]}
    assert "ComfyUI error at seed 11" in by_seed[11]["error"]
    assert by_seed[11]["mesh_path"] is None
    assert by_seed[10]["mesh_path"] and by_seed[12]["mesh_path"]
    assert result["ensemble"]["failed"] == 1
    assert len(result["ensemble"]["kept"]) == 2


def test_every_candidate_failing_fails_the_job(tmp_path):
    client = RecordingClient(tmp_path, fail_seeds={1, 2})
    backend = _backend(Trellis2Backend, client)
    with pytest.raises(BackendError) as exc:
        backend.generate_candidates(str(tmp_path / "p.png"), {},
                                    str(tmp_path / "o.glb"), [1, 2])
    assert "every candidate failed" in str(exc.value)
    assert client.unstaged, "staged inputs leaked on failure"


def test_cancelling_stops_the_fan_out(tmp_path):
    event = threading.Event()
    client = RecordingClient(tmp_path, cancel_event=event, cancel_after=2)
    backend = _backend(Trellis2Backend, client)
    with pytest.raises(Cancelled):
        backend.generate_candidates(str(tmp_path / "p.png"), {},
                                    str(tmp_path / "o.glb"), [1, 2, 3],
                                    cancel_event=event)
    assert len(client.graphs) == 2
    assert client.unstaged


def test_the_fan_out_refuses_a_picker_alongside_it(tmp_path):
    backend = _backend(Trellis2Backend, RecordingClient(tmp_path))
    with pytest.raises(BackendError) as exc:
        backend.generate_candidates(str(tmp_path / "p.png"),
                                    {"ensemble": {"best_of": 3}},
                                    str(tmp_path / "o.glb"), [1, 2])
    assert "one or the other" in str(exc.value)


def test_generate_ensemble_end_to_end(live, image, tmp_path):
    client, _app = live
    out = tmp_path / "cands.glb"
    status, body = client.post("/generate_ensemble", {
        "image_path": str(image), "n": 3, "output": str(out),
        "options": {"seed": 200}})
    assert status == 202, body
    assert body["mode"] == "candidates"
    assert body["ensemble"] == {"tier": "candidates", "n": 3,
                                "seeds": [200, 201, 202], "scored": False}
    assert body["seed"] == {"value": 200, "source": "caller"}
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    assert job["mesh_path"] is None
    assert [c["seed"] for c in job["candidates"]] == [200, 201, 202]
    paths = [c["mesh_path"] for c in job["candidates"]]
    assert len(set(paths)) == 3 and all(Path(p).is_file() for p in paths)
    assert job["ensemble"]["scored"] is False and job["ensemble"]["winner"] is None


def test_generate_ensemble_draws_its_base_seed_when_none_is_given(live, image):
    client, _app = live
    status, body = client.post("/generate_ensemble", {"image_path": str(image), "n": 2})
    assert status == 202, body
    base = body["seed"]["value"]
    assert body["seed"]["source"] == "drawn"
    assert body["ensemble"]["seeds"] == [base, base + 1]


def test_generate_ensemble_takes_an_explicit_seed_list(live, image):
    client, _app = live
    status, body = client.post("/generate_ensemble", {"image_path": str(image),
                                                      "seeds": [8, 3]})
    assert status == 202, body
    assert body["ensemble"]["seeds"] == [8, 3]
    assert body["seed"] == {"value": 8, "source": "caller"}
    job = client.wait(body["job_id"])
    assert [c["seed"] for c in job["candidates"]] == [8, 3]


@pytest.mark.parametrize("extra,needle", [
    ({}, "required"),
    ({"n": 0}, "1 to 9"),
    ({"n": 12}, "1 to 9"),
    ({"seeds": [4, 4]}, "repeat"),
    ({"seeds": [1], "options": {"seed": 2}}, "one or the other"),
    ({"n": 2, "options": {"ensemble": {"best_of": 2}}}, "one or the other"),
])
def test_a_malformed_generate_ensemble_is_a_400_before_anything_is_queued(
        live, image, extra, needle):
    client, app = live
    status, body = client.post("/generate_ensemble", {"image_path": str(image), **extra})
    assert status == 400, body
    assert needle in body["error"]
    assert app.queue.qsize() == 0 and not app.store.all()


# ==========================================================================
# 4. backend choice
# ==========================================================================
def test_the_shipped_default_backend_is_still_trellis2():
    shipped = json.loads((WORKFLOWS.parent / "config.json").read_text(encoding="utf-8"))
    assert shipped["default_backend"] == "trellis2"


@pytest.mark.parametrize("route,extra", [("/generate3d", {}),
                                         ("/generate_ensemble", {"n": 2})])
def test_an_unknown_backend_is_a_400_naming_the_real_ones(live, image, route, extra):
    client, app = live
    status, body = client.post(route, {"image_path": str(image),
                                       "backend": "hunyuan", **extra})
    assert status == 400, body
    assert "hunyuan" in body["error"]
    assert {"trellis2", "pixal3d", "fake"} <= set(body["available_backends"])
    assert app.queue.qsize() == 0


@pytest.mark.parametrize("bad", [7, ["trellis2"], {"name": "trellis2"}])
def test_a_non_string_backend_is_a_400(live, image, bad):
    client, _app = live
    status, body = client.post("/generate3d", {"image_path": str(image), "backend": bad})
    assert status == 400, body
    assert "backend must be a string" in body["error"]


def test_an_explicit_backend_reaches_the_job(live, image):
    client, _app = live
    status, body = client.post("/generate_ensemble", {"image_path": str(image),
                                                      "backend": "fake", "n": 1})
    assert status == 202 and body["backend"] == "fake"


# ==========================================================================
# 5. /capabilities
# ==========================================================================
def test_capabilities_declares_each_backends_conditioning(live):
    client, _app = live
    status, body = client.get("/capabilities")
    assert status == 200, body
    assert body["default_backend"] == "fake"
    backends = body["backends"]
    assert [n for n, b in backends.items() if b["default"]] == ["fake"]

    trellis = backends["trellis2"]["conditioning"]
    assert trellis["single_image"]["supported"] is True
    assert trellis["multi_view"]["supported"] is False

    pixal = backends["pixal3d"]["conditioning"]
    assert pixal["single_image"]["supported"] is True
    assert pixal["multi_view"]["supported"] is True
    assert pixal["multi_view"]["view_counts"] == [2, 3, 4]
    assert set(pixal["multi_view"]["views"]) >= {"front", "side", "back", "left"}
    assert pixal["multi_view"]["node"] == "Pixal3DMultiViewConditioning"
    # available is the machine's verdict (the scratch config has no weights),
    # and a "no" must name what is missing rather than just say no
    if not pixal["multi_view"]["available"]:
        assert pixal["multi_view"]["missing"]


def test_capabilities_declares_the_seed_and_ensemble_surface(live):
    client, _app = live
    _status, body = client.get("/capabilities")
    trellis = body["backends"]["trellis2"]
    assert trellis["seed"]["range"] == [0, bbase.SEED_MAX]
    assert set(trellis["seed"]["samplers"]) == {"structure", "shape", "upsample", "texture"}
    assert trellis["ensemble"]["candidates"]["range"] == list(bbase.CANDIDATE_LIMITS)
    assert trellis["ensemble"]["picked"]["tiers"] == {"structure_n": [1, 9],
                                                      "best_of": [1, 5]}
    assert {"seed", "texture_seed", "ensemble", "hard_surface"} <= set(trellis["options"])
    assert "POST /generate_ensemble" in body["endpoints"]


def test_capabilities_never_touches_the_model_host(live, monkeypatch):
    _client, app = live

    def boom(*a, **k):
        raise AssertionError("/capabilities started or queried ComfyUI")
    monkeypatch.setattr(app.client, "ensure_running", boom)
    monkeypatch.setattr(app.client, "object_info", boom, raising=False)
    payload = app.capabilities()
    assert "trellis2" in payload["backends"]
