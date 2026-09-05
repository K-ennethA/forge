"""Tests for the Forge Assistant bridge.

Run them with the geometry service's interpreter (pytest already lives there)::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

Nothing here talks to the real Claude CLI except the single, deliberately tiny
smoke turn at the bottom, which is skipped unless ``FORGE_ASSISTANT_SMOKE=1``.
Everything else points ``FORGE_ASSISTANT_CLAUDE`` at ``fake_claude.py``, which
validates the command line the bridge built and prints canned JSON.  That is the
point: the flags are the product here, so the fake asserts on them and the tests
assert on the fake's log.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ASSISTANT_DIR, os.pardir))
BRIDGE_PY = os.path.join(ASSISTANT_DIR, "bridge.py")
FAKE_CLI = os.path.join(TESTS_DIR, "fake_claude.py")

if ASSISTANT_DIR not in sys.path:
    sys.path.insert(0, ASSISTANT_DIR)

import bridge  # noqa: E402  (needs the path fix above)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Client(object):
    """Tiny HTTP client for the bridge under test."""

    def __init__(self, port, log_path):
        self.port = port
        self.log_path = log_path

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def request(self, path, payload=None, method=None, timeout=30.0):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url(path), data=data, headers=headers,
            method=method or ("POST" if data is not None else "GET"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, {"raw": body}

    # -- convenience -----------------------------------------------------
    def ask(self, message, context=None, conversation="continue"):
        status, body = self.request("/ask", {
            "message": message,
            "context": context if context is not None else {"active_object": "Cup"},
            "conversation": conversation,
        })
        return status, body

    def wait(self, job_id, timeout=60.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body = self.request("/job/%s" % job_id)
            if body.get("state") != "running":
                return body
            time.sleep(0.05)
        raise AssertionError("job %s never left running" % job_id)

    def turn(self, message, **kwargs):
        status, body = self.ask(message, **kwargs)
        assert status == 200, body
        return self.wait(body["job_id"])

    def invocations(self):
        """Every argv the fake CLI saw, in order."""
        if not os.path.isfile(self.log_path):
            return []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


def start_bridge(tmp_path, env_extra=None, claude=FAKE_CLI, python_exe=None):
    port = free_port()
    log_path = str(tmp_path / "fake_claude.log")
    env = dict(os.environ)
    env.update({
        "FORGE_ASSISTANT_PORT": str(port),
        "FORGE_ASSISTANT_CLAUDE": claude,
        "FORGE_ASSISTANT_CWD": REPO_ROOT,
        "FAKE_CLAUDE_LOG": log_path,
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("FORGE_ASSISTANT_MODEL", None)
    env.pop("FORGE_ASSISTANT_TOOLS", None)
    env.update(env_extra or {})

    proc = subprocess.Popen(
        [python_exe or sys.executable, BRIDGE_PY, "--port", str(port)],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    client = Client(port, log_path)
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")
            raise AssertionError("bridge died at startup:\n%s\n%s" % (out, err))
        try:
            status, _body = client.request("/health", timeout=2.0)
            if status == 200:
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        raise AssertionError("bridge never answered /health on port %d" % port)
    return proc, client


@pytest.fixture
def bridge_proc(tmp_path):
    """A bridge with the fake CLI, torn down after the test."""
    started = []

    def factory(**kwargs):
        proc, client = start_bridge(tmp_path, **kwargs)
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
def client(bridge_proc):
    return bridge_proc()


# ---------------------------------------------------------------------------
# pure units — the argv template and the parsing, with no process at all
# ---------------------------------------------------------------------------

def test_build_argv_carries_the_flags_the_assistant_needs():
    argv = bridge.build_argv("C:\\claude.exe", "hello", session_id=None,
                             permission_mode="auto")
    assert argv[0] == "C:\\claude.exe"
    assert argv[1:3] == ["-p", "hello"]
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    tools = argv[argv.index("--allowedTools") + 1]
    assert "mcp__forge__*" in tools, tools
    assert "Read" in tools and "Glob" in tools and "Grep" in tools
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert "--dangerously-skip-permissions" not in argv
    prompt_file = argv[argv.index("--append-system-prompt-file") + 1]
    assert os.path.isfile(prompt_file)
    assert "--resume" not in argv


def test_build_argv_resumes_and_honours_the_model_env(monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_MODEL", "haiku")
    argv = bridge.build_argv("claude", "hi", session_id="abc-123",
                             permission_mode="acceptEdits")
    assert argv[argv.index("--resume") + 1] == "abc-123"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_build_argv_omits_permission_mode_when_asked():
    argv = bridge.build_argv("claude", "hi", permission_mode=None)
    assert "--permission-mode" not in argv


def test_allowed_tools_env_can_be_emptied(monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_TOOLS", "")
    argv = bridge.build_argv("claude", "hi")
    assert argv[argv.index("--allowedTools") + 1] == ""


def test_context_rides_in_the_message_under_the_divider():
    prompt = bridge.build_prompt("segment this", {
        "active_object": "Cup", "objects": ["Cup", "Light"],
        "script_path": "C:\\parts\\cup.py"})
    assert prompt.startswith("segment this")
    assert bridge.CONTEXT_DIVIDER in prompt
    assert "Active object: Cup" in prompt
    assert "Objects in the scene: Cup, Light" in prompt
    assert "C:\\parts\\cup.py" in prompt
    # order matters: the message first, the context after it
    assert prompt.index("segment this") < prompt.index(bridge.CONTEXT_DIVIDER)


def test_no_context_means_no_divider():
    assert bridge.build_prompt("hello", None) == "hello"
    assert bridge.build_prompt("hello", {}) == "hello"


def test_salvage_json_survives_a_warning_line():
    payload = bridge.salvage_json(
        '(node:1) Warning: whatever\n{"result": "hi", "session_id": "s1"}\n')
    assert payload["result"] == "hi"


def test_salvage_json_gives_up_honestly():
    assert bridge.salvage_json("not json\nstill not json") is None
    assert bridge.salvage_json("") is None


def test_the_two_failures_an_artist_hits_get_rewritten():
    signed_out = bridge.friendly_error("Not logged in · Please run /login")
    assert "Open a terminal" in signed_out and "/login" in signed_out
    limited = bridge.friendly_error("Claude AI usage limit reached")
    assert "usage limit" in limited and "nothing in your scene was changed" in limited
    # anything else is passed through honestly rather than guessed at
    assert bridge.friendly_error("build123d exploded") == "build123d exploded"


def test_launcher_runs_a_py_override_under_this_interpreter():
    assert bridge.launcher(FAKE_CLI) == [sys.executable, FAKE_CLI]
    assert bridge.launcher("C:\\claude.exe") == ["C:\\claude.exe"]


# ---------------------------------------------------------------------------
# the live bridge
# ---------------------------------------------------------------------------

def test_health_finds_the_cli_and_reports_its_version(client):
    status, body = client.request("/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["claude_cli"]["found"] is True
    assert body["claude_cli"]["version"].startswith("9.9.9")
    assert body["busy"] is False


def test_ask_then_poll_reaches_done(client):
    reply = client.turn("segment this into 4",
                        context={"active_object": "Cup", "script_path": "cup.py"})
    assert reply["state"] == "done", reply
    assert "OK" in reply["reply"]
    assert reply["session_id"] == "sess-fake-0001"
    assert reply["cost_usd"] == pytest.approx(0.0123)
    assert reply["duration_ms"] >= 0

    calls = client.invocations()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    prompt = argv[argv.index("-p") + 1]
    assert "segment this into 4" in prompt
    assert bridge.CONTEXT_DIVIDER in prompt
    assert "Active object: Cup" in prompt
    assert "mcp__forge__*" in argv[argv.index("--allowedTools") + 1]
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert os.path.normcase(calls[0]["cwd"]) == os.path.normcase(REPO_ROOT)


def test_second_turn_resumes_the_session(client):
    first = client.turn("hello")
    assert "--resume" not in client.invocations()[0]["argv"]

    second = client.turn("and now split it")
    assert second["state"] == "done"
    argv = client.invocations()[1]["argv"]
    assert argv[argv.index("--resume") + 1] == first["session_id"]


def test_new_conversation_drops_the_session(client):
    client.turn("hello")
    client.turn("second", conversation="continue")
    assert "--resume" in client.invocations()[1]["argv"]

    client.turn("start over", conversation="new")
    assert "--resume" not in client.invocations()[2]["argv"]

    client.turn("carry on")
    assert "--resume" in client.invocations()[3]["argv"]


def test_new_endpoint_also_clears_the_session(client):
    client.turn("hello")
    status, body = client.request("/new", {})
    assert status == 200 and body["session"] is None
    client.turn("again")
    assert "--resume" not in client.invocations()[1]["argv"]


def test_one_job_at_a_time(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "slow",
                                    "FAKE_CLAUDE_SLEEP": "6"})
    status, first = client.ask("a long one")
    assert status == 200

    status, busy = client.ask("me too")
    assert status == 409, busy
    assert "still working" in busy["error"]
    assert busy["job_id"] == first["job_id"]

    _status, health = client.request("/health")
    assert health["busy"] is True

    client.request("/cancel/%s" % first["job_id"], {})


def test_cancel_stops_the_run(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "slow",
                                    "FAKE_CLAUDE_SLEEP": "30"})
    status, started = client.ask("something slow")
    assert status == 200

    time.sleep(0.5)
    status, body = client.request("/cancel/%s" % started["job_id"], {})
    assert status == 200, body

    final = client.wait(started["job_id"], timeout=30.0)
    assert final["state"] == "cancelled", final

    # and the bridge is usable again straight afterwards
    _status, health = client.request("/health")
    assert health["busy"] is False


def test_cancel_of_an_unknown_job_is_a_clean_404(client):
    status, body = client.request("/cancel/nope", {})
    assert status == 404 and "No such job" in body["error"]


def test_a_warning_line_before_the_json_is_salvaged(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "noise"})
    reply = client.turn("hello")
    assert reply["state"] == "done", reply
    assert "OK" in reply["reply"]


def test_unreadable_output_becomes_a_readable_error(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "garbage"})
    reply = client.turn("hello")
    assert reply["state"] == "error", reply
    assert "did not return readable JSON" in reply["error"]
    assert "sideways" in reply["error"]  # the stderr tail is carried through


def test_an_api_error_is_reported_not_swallowed(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "api_error"})
    reply = client.turn("hello")
    assert reply["state"] == "error", reply
    assert "refused" in reply["error"]


def test_an_older_cli_that_rejects_permission_mode_falls_back(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "reject_permission"})
    reply = client.turn("hello")
    assert reply["state"] == "done", reply

    calls = client.invocations()
    assert len(calls) == 2, [c["argv"] for c in calls]
    assert calls[0]["argv"][calls[0]["argv"].index("--permission-mode") + 1] == "auto"
    assert calls[1]["argv"][calls[1]["argv"].index("--permission-mode") + 1] == "acceptEdits"


def test_a_missing_cli_is_said_plainly(bridge_proc):
    client = bridge_proc(claude=os.path.join(TESTS_DIR, "no_such_claude_here.exe"))
    status, health = client.request("/health")
    assert health["claude_cli"]["found"] is False
    assert "not found" in health["claude_cli"]["hint"]

    status, body = client.request("/ask", {"message": "hi"})
    assert status == 503
    assert "claude.com/claude-code" in body["error"]


def test_an_empty_message_is_refused(client):
    status, body = client.request("/ask", {"message": "   "})
    assert status == 400 and "Type something" in body["error"]


def test_unknown_paths_are_json_404s(client):
    status, body = client.request("/nope")
    assert status == 404 and "Unknown path" in body["error"]
    status, body = client.request("/job/does-not-exist")
    assert status == 404


def test_it_survives_pythonw_which_has_no_stderr(bridge_proc):
    """``start_forge`` launches this with ``pythonw.exe`` so no window appears.

    Under pythonw ``sys.stderr`` is ``None``; a bridge that writes a startup
    banner straight to it dies before it ever binds the port.  It did, once.
    """
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pytest.skip("no pythonw.exe next to %s" % sys.executable)
    client = bridge_proc(python_exe=pythonw)
    status, body = client.request("/health")
    assert status == 200 and body["status"] == "ok"
    assert client.turn("hello")["state"] == "done"


def test_the_allowed_tools_env_reaches_the_cli(bridge_proc):
    client = bridge_proc(env_extra={"FORGE_ASSISTANT_TOOLS": "Read",
                                    "FAKE_CLAUDE_EXPECT_TOOLS": "Read"})
    reply = client.turn("hello")
    assert reply["state"] == "done", reply
    argv = client.invocations()[0]["argv"]
    assert argv[argv.index("--allowedTools") + 1] == "Read"


# ---------------------------------------------------------------------------
# one real turn, on purpose kept as small as it can be
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("FORGE_ASSISTANT_SMOKE") != "1",
                    reason="set FORGE_ASSISTANT_SMOKE=1 to spend a real turn")
def test_smoke_one_real_turn(tmp_path):
    """One haiku turn with no tools: does the real CLI come back at all?

    Auth or rate-limit failures here are a finding about the machine, not a bug
    in the bridge, so the assertion message says which is which.
    """
    real = bridge.resolve_claude()
    if not real:
        pytest.skip("no claude CLI on this machine")

    proc, client = start_bridge(tmp_path, claude=real, env_extra={
        "FORGE_ASSISTANT_MODEL": "haiku",
        "FORGE_ASSISTANT_TOOLS": "",
        "FORGE_ASSISTANT_TIMEOUT": "180",
    })
    try:
        status, started = client.ask("Reply with exactly: OK", context=None)
        assert status == 200, started
        reply = client.wait(started["job_id"], timeout=200.0)

        if reply["state"] == "error":
            error = str(reply.get("error") or "")
            # Auth and rate limits are facts about this machine, not bugs in the
            # bridge: report them, do not fail on them.
            if any(word in error.lower() for word in
                   ("sign", "login", "rate limit", "usage limit", "credit")):
                pytest.skip("real CLI unavailable: %s" % error.splitlines()[0])
        assert reply["state"] == "done", reply.get("error")
        assert "OK" in reply["reply"], reply["reply"]
        assert reply.get("session_id")
        print("\nsmoke turn: %r  cost=%s" % (reply["reply"][:120], reply.get("cost_usd")))
    finally:
        proc.terminate()
