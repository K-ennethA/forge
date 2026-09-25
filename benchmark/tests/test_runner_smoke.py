"""runner.run_task against a STUB bridge — never the real one.

A local ``ThreadingHTTPServer`` on an OS-assigned port answers ``/ask``,
``/job/<id>`` and ``/cancel/<id>`` with the bridge's own shapes
(``assistant/bridge.py``: ``/ask`` -> ``{"job_id", "state"}``, ``/job/<id>`` ->
``{"state", "duration_ms"?, ...}``, four terminal states).  Measurement is
injected, so no Blender runs here; the real measurement path is proven by
``addon/tests/headless_benchmark.py``.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark import quality, runner  # noqa: E402
from benchmark.report import validate_report  # noqa: E402

#: The token the stub demands, as the real bridge demands its own (review
#: finding 1): every POST must carry ``Authorization: Bearer <token>``, and the
#: runner reads it from ``FORGE_ASSISTANT_TOKEN_FILE``.
STUB_TOKEN = "stub-token-0123456789"


@pytest.fixture(autouse=True)
def token_file(tmp_path, monkeypatch):
    """Point the runner at a token file holding the stub's token."""
    path = tmp_path / "bridge-token"
    path.write_text(STUB_TOKEN, encoding="ascii")
    monkeypatch.setenv("FORGE_ASSISTANT_TOKEN_FILE", str(path))
    return path


class StubBridge:
    """Scripted bridge. ``script`` maps a poll index to a (status, body) reply."""

    def __init__(self, finish_after_s=0.3, final_state="done", artifact=None,
                 ask_status=200, fail_polls=0, never_finish=False):
        self.refused = []
        self.finish_after_s = finish_after_s
        self.final_state = final_state
        self.artifact = artifact
        self.ask_status = ask_status
        self.fail_polls = fail_polls
        self.never_finish = never_finish
        self.asks = []
        self.polls = 0
        self.cancels = []
        self.started = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.headers.get("Authorization") != "Bearer %s" % STUB_TOKEN:
                    stub.refused.append(self.path)
                    self._send(401, {"error": "This bridge needs its token"})
                    return
                if self.headers.get("Content-Type") != "application/json":
                    self._send(415, {"error": "Send this as application/json"})
                    return
                if self.path == "/ask":
                    stub.asks.append(body)
                    if stub.ask_status != 200:
                        self._send(stub.ask_status, {"error": "One message is already waiting",
                                                     "state": "queued"})
                        return
                    stub.started = time.monotonic()
                    self._send(200, {"job_id": "job-1", "state": "running", "queued": False})
                elif self.path.startswith("/cancel/"):
                    stub.cancels.append(self.path[len("/cancel/"):])
                    self._send(200, {"state": "cancelled"})
                else:
                    self._send(404, {"error": "no route"})

            def do_GET(self):
                if not self.path.startswith("/job/"):
                    self._send(404, {"error": "no route"})
                    return
                stub.polls += 1
                if stub.polls <= stub.fail_polls:
                    self._send(500, {"error": "transient"})
                    return
                elapsed = time.monotonic() - stub.started
                if stub.never_finish or elapsed < stub.finish_after_s:
                    self._send(200, {"job_id": "job-1", "state": "running", "activity": []})
                    return
                if stub.artifact and stub.final_state == "done" and not os.path.exists(stub.artifact):
                    with open(stub.artifact, "w") as handle:
                        handle.write("solid stub\nendsolid stub\n")
                self._send(200, {"job_id": "job-1", "state": stub.final_state,
                                 "duration_ms": int(elapsed * 1000), "cost_usd": 0.42,
                                 "model": "stub-model", "num_turns": 3,
                                 "reply": "built it", "activity": []})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def write_task(tmp_path, artifact, timeout_s=60, image=True):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task = {"name": "stub-task", "version": 1, "mode": "part", "timeout_s": timeout_s,
            "prompt": "make a stub", "artifact": str(artifact),
            "gates": {"parts": [{"match": None, "count": 1}]},
            "expected": {"height_mm": {"kind": "overall_dim", "axis": "z",
                                       "expected": 10.0, "tol": 0.1}}}
    if image:
        (task_dir / "ref.png").write_bytes(b"\x89PNG stub")
        task["image"] = "ref.png"
    (task_dir / "task.json").write_text(json.dumps(task))
    return str(task_dir)


class FakeEvaluate:
    def __init__(self):
        self.calls = []

    def __call__(self, task, artifact, log_path=None):
        self.calls.append(artifact)
        measurement = {"gate_rows": [{"part": "p", "check": c, "status": "pass", "passed": True}
                                     for c in quality.DEFAULT_GATE_CHECKS],
                       "measured": {"height_mm": {"value": 10.02}}}
        out = quality.grade(task, measurement)
        out.update(measure=measurement, measure_seconds=0.01)
        return out


def test_done_run_polls_times_and_writes_a_valid_report(tmp_path):
    artifact = tmp_path / "out.stl"
    out = tmp_path / "reports" / "baseline.json"
    fake = FakeEvaluate()
    with StubBridge(finish_after_s=0.3, artifact=str(artifact)) as stub:
        report = runner.run_task(write_task(tmp_path, artifact), str(out),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.05,
                                 evaluate=fake)
    # the request is the bridge's /ask shape, a fresh conversation, the frozen image
    ask = stub.asks[0]
    assert ask["message"] == "make a stub" and ask["conversation"] == "new"
    assert ask["context"]["image_path"].endswith("ref.png")
    assert os.path.isabs(ask["context"]["image_path"])
    # polled until terminal, timed on the runner's clock
    assert stub.polls >= 3
    assert report["final_state"] == "done"
    assert 0.3 <= report["wall_seconds"] < 3.0
    assert report["bridge"]["duration_s"] >= 0.3 and report["bridge"]["cost_usd"] == 0.42
    # measured the artifact this run wrote
    assert fake.calls == [str(artifact)]
    assert report["artifact"]["fresh"] is True
    assert report["gates"] == {"passed": 4, "total": 4, "failures": []}
    assert report["fidelity"]["height_mm"]["pass"] is True
    assert report["score"] > 50.0
    assert report["appearance_score"] is None
    # written, and schema-valid on disk
    on_disk = json.loads(out.read_text())
    assert validate_report(on_disk) == []
    assert on_disk["wall_seconds"] == report["wall_seconds"]
    assert on_disk["git_sha"]


def test_stale_artifact_is_not_measured(tmp_path):
    artifact = tmp_path / "old.stl"
    artifact.write_text("solid old\nendsolid old\n")
    old = time.time() - 3600
    os.utime(artifact, (old, old))
    fake = FakeEvaluate()
    with StubBridge(finish_after_s=0.0) as stub:  # never writes the artifact
        report = runner.run_task(write_task(tmp_path, artifact, image=False),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=fake)
    assert "context" not in stub.asks[0]
    assert fake.calls == []
    assert report["artifact"] == {"path": str(artifact), "exists": True, "fresh": False,
                                  "mtime": report["artifact"]["mtime"]}
    assert report["gates"]["passed"] == 0 and report["gates"]["total"] == 4
    assert "stale artifact" in report["gates"]["failures"][0]
    assert report["fidelity"]["height_mm"]["pass"] is False
    assert report["score"] == 0.0


@pytest.mark.parametrize("state", ["error", "timeout", "cancelled"])
def test_every_bridge_terminal_state_stops_the_poll(tmp_path, state):
    with StubBridge(finish_after_s=0.1, final_state=state) as stub:
        report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl"),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == state
    assert report["artifact"]["exists"] is False
    assert validate_report(report) == []


def test_refused_ask_never_polls(tmp_path):
    with StubBridge(ask_status=409) as stub:
        report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl"),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == "refused"
    assert "already waiting" in report["error"]
    assert stub.polls == 0 and report["job_id"] is None


def test_runner_deadline_cancels_a_job_that_never_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "DEADLINE_GRACE_S", 0.0)
    with StubBridge(never_finish=True) as stub:
        report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl", timeout_s=0.3),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.05,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == "timeout" and report["runner_deadline_hit"] is True
    assert stub.cancels == ["job-1"]
    assert 0.3 <= report["wall_seconds"] < 2.0


def test_the_runner_sends_the_token_from_the_token_file(tmp_path, token_file):
    """Every POST carried it (the stub refuses one that does not)…"""
    with StubBridge(never_finish=True) as stub:
        client = runner.BridgeClient(stub.url)
        status, _body = client.ask("hi")
        assert status == 200 and stub.refused == []
        assert client.cancel("job-1")[0] == 200
        # …re-read per call: a restarted bridge's new token is picked up
        token_file.write_text("a-new-token", encoding="ascii")
        assert client.ask("again")[0] == 401
        assert stub.refused == ["/ask"]


def test_a_missing_token_is_a_refused_run_that_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_TOKEN_FILE", str(tmp_path / "absent"))
    with StubBridge() as stub:
        report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl"),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == "refused"
    assert "token" in report["error"]
    assert stub.asks == [] and stub.refused == ["/ask"]


def test_an_over_budget_turn_is_terminal(tmp_path):
    with StubBridge(finish_after_s=0.05, final_state="over_budget") as stub:
        report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl"),
                                 str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == "over_budget"
    assert report["runner_deadline_hit"] is False
    assert validate_report(report) == []


def test_transient_poll_failures_are_ridden_out(tmp_path):
    artifact = tmp_path / "out.stl"
    with StubBridge(finish_after_s=0.0, fail_polls=2, artifact=str(artifact)) as stub:
        report = runner.run_task(write_task(tmp_path, artifact), str(tmp_path / "r.json"),
                                 bridge=runner.BridgeClient(stub.url), poll_s=0.02,
                                 evaluate=FakeEvaluate())
    assert report["final_state"] == "done" and stub.polls == 3


def test_unreachable_bridge_is_a_refused_run(tmp_path):
    # a port nothing listens on: bind, read the number, close
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    report = runner.run_task(write_task(tmp_path, tmp_path / "none.stl"),
                             str(tmp_path / "r.json"),
                             bridge=runner.BridgeClient("http://127.0.0.1:%d" % port,
                                                        http_timeout=2.0),
                             poll_s=0.02, evaluate=FakeEvaluate())
    assert report["final_state"] == "refused" and report["error"]


def test_cli_entry_point(tmp_path, monkeypatch):
    artifact = tmp_path / "out.stl"
    out = tmp_path / "cli.json"
    real = runner.run_task
    fake = FakeEvaluate()
    seen = {}

    def with_fake_measure(task_dir, out_json, bridge=None, poll_s=1.0):
        seen["bridge"], seen["poll_s"] = bridge.base_url, poll_s
        return real(task_dir, out_json, bridge=bridge, poll_s=poll_s, evaluate=fake)

    monkeypatch.setattr(runner, "run_task", with_fake_measure)
    with StubBridge(finish_after_s=0.0, artifact=str(artifact)) as stub:
        code = runner.main([write_task(tmp_path, artifact), str(out),
                            "--bridge", stub.url, "--poll", "0.02"])
    assert code == 0 and seen == {"bridge": stub.url, "poll_s": 0.02}
    assert validate_report(json.loads(out.read_text())) == []
    assert fake.calls == [str(artifact)]
