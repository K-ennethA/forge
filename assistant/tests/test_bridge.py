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
    # stream-json (not json) is what makes the activity list possible, and the
    # CLI demands --verbose with it in -p mode.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert "--include-partial-messages" in argv
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


# ---------------------------------------------------------------------------
# Phase 6c — an attached reference image
# ---------------------------------------------------------------------------

def _png(tmp_path, name="sketch.png"):
    """A real (tiny) file on disk; the bridge only ever checks the path."""
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    return str(path)


def test_an_attached_image_becomes_its_own_block_at_the_end(tmp_path):
    image = _png(tmp_path)
    prompt = bridge.build_prompt("what is this?", {
        "active_object": "Cup", "image_path": image})

    assert bridge.IMAGE_DIVIDER in prompt
    assert image in prompt
    assert "View this image with the Read tool BEFORE answering." in prompt
    # the message first, then the scene, then the attachment: the instruction
    # to look at the picture is the last thing read before the work starts
    assert prompt.index("what is this?") < prompt.index(bridge.CONTEXT_DIVIDER)
    assert prompt.index(bridge.CONTEXT_DIVIDER) < prompt.index(bridge.IMAGE_DIVIDER)
    # and it is named once, not once as a context line and once as a block
    assert prompt.count(image) == 1
    assert "Image path:" not in prompt


def test_no_attachment_means_no_block(tmp_path):
    assert bridge.IMAGE_DIVIDER not in bridge.build_prompt("hi", {"active_object": "Cup"})
    assert bridge.IMAGE_DIVIDER not in bridge.build_prompt("hi", {"image_path": ""})
    assert bridge.IMAGE_DIVIDER not in bridge.build_prompt("hi", None)
    # an empty attachment must not resurrect the context divider either
    assert bridge.build_prompt("hi", {"image_path": "   "}) == "hi"


def test_an_attachment_alone_still_carries_the_message(tmp_path):
    image = _png(tmp_path)
    prompt = bridge.build_prompt("model this", {"image_path": image})
    assert prompt.startswith("model this")
    assert bridge.CONTEXT_DIVIDER not in prompt
    assert bridge.IMAGE_DIVIDER in prompt


def test_a_relative_or_expandable_path_is_absolute_in_the_prompt(tmp_path, monkeypatch):
    _png(tmp_path)
    monkeypatch.chdir(tmp_path)
    prompt = bridge.build_prompt("look", {"image_path": "sketch.png"})
    line = prompt.split(bridge.IMAGE_DIVIDER)[1].strip().splitlines()[0]
    assert os.path.isabs(line), line
    assert os.path.normcase(line) == os.path.normcase(str(tmp_path / "sketch.png"))


@pytest.mark.parametrize("extension", [".png", ".PNG", ".jpg", ".jpeg", ".webp", ".bmp"])
def test_every_readable_image_type_is_accepted(tmp_path, extension):
    assert bridge.image_error(_png(tmp_path, "ref" + extension)) == ""


def test_the_attachment_checks_say_what_is_wrong(tmp_path):
    assert bridge.image_error("") == ""
    assert bridge.image_error(None) == ""

    missing = bridge.image_error(str(tmp_path / "nope.png"))
    assert "There is no file at" in missing and "nope.png" in missing

    not_an_image = bridge.image_error(_png(tmp_path, "notes.txt"))
    assert "not an image" in not_an_image
    assert ".png" in not_an_image  # it says what IS accepted

    folder = tmp_path / "shots.png"
    folder.mkdir()
    assert "folder" in bridge.image_error(str(folder))


def test_an_attachment_reaches_the_cli_without_widening_the_allowlist(bridge_proc, tmp_path):
    image = _png(tmp_path)
    client = bridge_proc(env_extra={"FAKE_CLAUDE_EXPECT_IMAGE": image})
    reply = client.turn("what shape is this?",
                        context={"active_object": "Cup", "image_path": image})
    assert reply["state"] == "done", reply

    argv = client.invocations()[0]["argv"]
    prompt = argv[argv.index("-p") + 1]
    assert bridge.IMAGE_DIVIDER in prompt and image in prompt
    # the whole point of the design: Read already renders images, so nothing
    # about the permission surface changes to carry a picture
    tools = argv[argv.index("--allowedTools") + 1]
    assert tools == bridge.DEFAULT_ALLOWED_TOOLS
    assert tools == "Read,Glob,Grep,mcp__forge__*"


def test_a_turn_without_an_attachment_carries_no_block(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_EXPECT_IMAGE": ""})
    assert client.turn("no picture here")["state"] == "done"


def test_an_attachment_that_is_not_there_is_a_clean_400(client, tmp_path):
    status, body = client.request("/ask", {
        "message": "look at this",
        "context": {"image_path": str(tmp_path / "gone.png")}})
    assert status == 400, body
    assert "There is no file at" in body["error"]
    assert client.invocations() == []  # no turn was spent


def test_an_attachment_of_the_wrong_type_is_a_clean_400(client, tmp_path):
    path = tmp_path / "model.stl"
    path.write_bytes(b"solid\n")
    status, body = client.request("/ask", {
        "message": "look at this", "context": {"image_path": str(path)}})
    assert status == 400, body
    assert "not an image" in body["error"]
    assert ".webp" in body["error"]


def test_a_bad_attachment_does_not_leave_the_bridge_busy(client, tmp_path):
    client.request("/ask", {"message": "hi", "context": {"image_path": "C:\\nope.png"}})
    _status, health = client.request("/health")
    assert health["busy"] is False
    # and the next, valid, message goes through
    assert client.turn("carry on")["state"] == "done"


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
# Phase 6b — the event stream and the activity list
#
# The pure units first: every one of these runs with no process at all, because
# the parsing is where the "unknown shapes are skipped, never fatal" promise
# actually lives.
# ---------------------------------------------------------------------------

def test_parse_stream_line_skips_everything_that_is_not_an_object():
    assert bridge.parse_stream_line('{"type": "result"}') == {"type": "result"}
    assert bridge.parse_stream_line("  \n") is None
    assert bridge.parse_stream_line("(node:1) ExperimentalWarning") is None
    assert bridge.parse_stream_line('{"type": "stream_ev') is None  # half a line
    assert bridge.parse_stream_line('["not", "an", "object"]') is None


def test_looks_like_result_knows_the_final_event_from_the_rest():
    assert bridge.looks_like_result({"type": "result", "result": "hi"})
    assert not bridge.looks_like_result({"type": "stream_event", "event": {}})
    assert not bridge.looks_like_result({"type": "assistant", "message": {}})
    # a build that prints the plain --output-format json object instead
    assert bridge.looks_like_result({"result": "hi", "session_id": "s1"})
    assert not bridge.looks_like_result({})


def test_tool_labels_read_like_the_contract_example():
    assert bridge.tool_label("mcp__forge__partforge_check",
                             {"script_path": "C:\\parts\\part.py"}) \
        == "partforge_check: part.py"
    # no arguments worth showing: the bare name, not "name: {}"
    assert bridge.tool_label("mcp__forge__get_scene_info", {}) == "get_scene_info"
    assert bridge.tool_label("Read", None) == "Read"


def test_argument_summaries_stay_inside_sixty_characters():
    summary = bridge.summarize_args({
        "overrides": {"bowl_diameter": 152.4, "wall_thickness": 3.2, "feet": 4},
        "joint": {"type": "dovetail", "tolerance": 0.2},
        "include_mesh": True,
    })
    assert len(summary) <= bridge.TOOL_ARG_LIMIT, summary
    assert summary.endswith("…")
    assert "overrides" in summary
    # a multi-line value is flattened; a sidebar has one line per entry
    assert "\n" not in bridge.summarize_args({"code": "import bpy\nprint(1)"})


def _store_with_job():
    store = bridge.JobStore()
    job, _busy = store.start("hello")
    return store, job["job_id"]


def _tool_events(index, tool_id, name, args):
    """The events a CLI emits for one tool call, name first, input after."""
    return [
        {"type": "stream_event", "event": {
            "type": "content_block_start", "index": index,
            "content_block": {"type": "tool_use", "id": tool_id, "name": name,
                              "input": {}}}},
        {"type": "stream_event", "event": {
            "type": "content_block_delta", "index": index,
            "delta": {"type": "input_json_delta",
                      "partial_json": json.dumps(args)}}},
        {"type": "stream_event", "event": {
            "type": "content_block_stop", "index": index}},
    ]


def _text_event(text):
    return {"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 9,
        "delta": {"type": "text_delta", "text": text}}}


def test_a_tool_call_is_one_line_that_gains_its_arguments():
    store, job_id = _store_with_job()
    recorder = bridge.ActivityRecorder(store, job_id, interval=0)
    for event in _tool_events(0, "toolu_1", "mcp__forge__partforge_check",
                              {"script_path": "projects/cup/part.py"}):
        recorder.feed(event)

    activity = store.snapshot(job_id)["activity"]
    assert len(activity) == 1, activity  # not one line for the name and one for the input
    assert activity[0]["kind"] == "tool"
    assert activity[0]["label"] == "partforge_check: part.py"
    assert isinstance(activity[0]["t"], float)


def test_the_thinking_marker_appears_once_and_only_after_tools():
    store, job_id = _store_with_job()
    recorder = bridge.ActivityRecorder(store, job_id, interval=0)
    for event in _tool_events(0, "toolu_1", "mcp__forge__get_scene_info", {}):
        recorder.feed(event)
    recorder.feed(_text_event("Done — "))
    recorder.feed(_text_event("four wedges."))

    kinds = [entry["kind"] for entry in store.snapshot(job_id)["activity"]]
    assert kinds == ["tool", "status", "text", "text"], kinds
    assert store.snapshot(job_id)["activity"][1]["label"] == "thinking…"

    # text with no tools before it is just text: nothing to announce
    store2, job2 = _store_with_job()
    plain = bridge.ActivityRecorder(store2, job2, interval=0)
    plain.feed(_text_event("hello"))
    assert [e["kind"] for e in store2.snapshot(job2)["activity"]] == ["text"]


def test_text_markers_are_throttled():
    store, job_id = _store_with_job()
    recorder = bridge.ActivityRecorder(store, job_id, interval=2.0)
    for chunk in ("one ", "two ", "three ", "four ", "five ", "six"):
        recorder.feed(_text_event(chunk))

    activity = store.snapshot(job_id)["activity"]
    assert len(activity) == 1, activity  # six deltas in well under two seconds
    assert activity[0]["label"] == "one"
    # ... and nothing was lost: the whole text is still there for the salvage path
    assert recorder.text() == "one two three four five six"


def test_an_unknown_event_shape_is_skipped_not_fatal():
    store, job_id = _store_with_job()
    recorder = bridge.ActivityRecorder(store, job_id, interval=0)
    for junk in (None, [], {"type": "wat"}, {"type": "stream_event"},
                 {"type": "stream_event", "event": "not a dict"},
                 {"type": "assistant", "message": {"content": "nope"}},
                 {"type": "stream_event", "event": {"type": "content_block_delta",
                                                    "delta": {"type": "audio"}}}):
        recorder.feed(junk)
    assert store.snapshot(job_id)["activity"] == []
    # and the recorder still works afterwards
    recorder.feed(_text_event("still here"))
    assert store.snapshot(job_id)["activity"][0]["label"] == "still here"


def test_activity_is_capped_at_two_hundred_keeping_the_ends():
    store, job_id = _store_with_job()
    for index in range(320):
        store.add_activity(job_id, "tool", "step %d" % index)

    activity = store.snapshot(job_id)["activity"]
    assert len(activity) == bridge.ACTIVITY_LIMIT
    # how it started
    assert activity[0]["label"] == "step 0"
    assert activity[bridge.ACTIVITY_HEAD - 1]["label"] == "step %d" % (bridge.ACTIVITY_HEAD - 1)
    # the middle is gone, and says so
    marker = activity[bridge.ACTIVITY_HEAD]
    assert marker["kind"] == "status" and "not shown" in marker["label"]
    # what it is doing now
    assert activity[-1]["label"] == "step 319"
    assert store.snapshot(job_id)["activity_dropped"] == 320 - (bridge.ACTIVITY_LIMIT - 1)


# --- the same thing, end to end through a real subprocess -------------------

def _stream_client(bridge_proc, mode="stream", **env):
    extra = {"FAKE_CLAUDE_MODE": mode}
    extra.update(env)
    return bridge_proc(env_extra=extra)


def test_a_streamed_turn_lands_the_reply_and_the_activity(bridge_proc):
    client = _stream_client(bridge_proc)
    final = client.turn("segment this into 4")
    assert final["state"] == "done", final
    assert "four wedges" in final["reply"] or "4 wedges" in final["reply"], final["reply"]
    assert final["session_id"] == "sess-fake-0001"
    assert final["cost_usd"] == pytest.approx(0.0123)

    activity = final["activity"]
    kinds = [entry["kind"] for entry in activity]
    labels = [entry["label"] for entry in activity]
    assert kinds[:3] == ["tool", "tool", "status"], labels
    # the contract's own example, produced by arguments that arrived as deltas
    assert labels[0] == "partforge_check: part.py", labels
    assert labels[1].startswith("partforge_segment: "), labels
    assert len(labels[1]) <= len("partforge_segment: ") + bridge.TOOL_ARG_LIMIT
    assert labels[2] == "thinking…"
    assert kinds[3:] and set(kinds[3:]) == {"text"}, kinds
    # the malformed event the fake printed between the tools was skipped, not fatal
    assert len(activity) >= 4
    # timestamps only ever move forward
    assert [entry["t"] for entry in activity] == sorted(entry["t"] for entry in activity)


def test_the_finished_job_keeps_its_activity(bridge_proc):
    client = _stream_client(bridge_proc)
    final = client.turn("hello")
    again = client.request("/job/%s" % final["job_id"])[1]
    assert again["state"] == "done"
    assert again["activity"] == final["activity"]


def test_activity_grows_while_the_job_is_still_running(bridge_proc):
    """The whole point: the artist sees the tools before the answer arrives."""
    client = _stream_client(bridge_proc, mode="stream_slow",
                            FAKE_CLAUDE_SLEEP="6")
    status, started = client.ask("something slow")
    assert status == 200, started

    deadline = time.time() + 20.0
    seen = []
    while time.time() < deadline:
        _status, body = client.request("/job/%s" % started["job_id"])
        seen = body.get("activity") or []
        if body.get("state") != "running":
            raise AssertionError("the job finished before we could watch it")
        if len(seen) >= 2:
            break
        time.sleep(0.1)

    assert len(seen) >= 2, seen
    assert seen[0]["label"] == "partforge_check: part.py", seen
    assert all(entry["kind"] == "tool" for entry in seen[:2]), seen

    client.request("/cancel/%s" % started["job_id"], {})


def test_cancel_mid_stream_stops_it_and_keeps_what_was_seen(bridge_proc):
    client = _stream_client(bridge_proc, mode="stream_slow",
                            FAKE_CLAUDE_SLEEP="30")
    status, started = client.ask("cut this up")
    assert status == 200

    deadline = time.time() + 20.0
    while time.time() < deadline:
        _status, body = client.request("/job/%s" % started["job_id"])
        if len(body.get("activity") or []) >= 2:
            break
        time.sleep(0.1)

    status, _body = client.request("/cancel/%s" % started["job_id"], {})
    assert status == 200
    final = client.wait(started["job_id"], timeout=30.0)
    assert final["state"] == "cancelled", final
    assert len(final["activity"]) >= 2, final["activity"]
    assert final["activity"][0]["kind"] == "tool"

    _status, health = client.request("/health")
    assert health["busy"] is False


def test_a_stream_with_no_result_event_is_salvaged(bridge_proc):
    client = _stream_client(bridge_proc, mode="stream_noresult")
    final = client.turn("segment this")
    assert final["state"] == "done", final
    # the reply is the text that streamed past, not an apology
    assert "wedges" in final["reply"], final["reply"]
    assert final["activity"], final


def test_the_text_throttle_is_configurable_end_to_end(bridge_proc):
    """Same six chunks, two throttle settings, two different activity lists."""
    client = _stream_client(bridge_proc)
    slow = [e for e in client.turn("a")["activity"] if e["kind"] == "text"]
    assert len(slow) == 1, slow

    fast_client = _stream_client(bridge_proc,
                                 FORGE_ASSISTANT_TEXT_INTERVAL="0")
    fast = [e for e in fast_client.turn("b")["activity"] if e["kind"] == "text"]
    assert len(fast) > len(slow), fast


def test_a_json_only_cli_still_answers_with_an_empty_activity_list(client):
    """The old single-object output: no events, so nothing to show — not a crash."""
    final = client.turn("hello")
    assert final["state"] == "done"
    assert final["activity"] == []


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
