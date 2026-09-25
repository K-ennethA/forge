"""The bridge's safety and lifecycle hardening (docs/research/review-assistant.md).

Four findings, each gated here end-to-end against a real bridge process and
the fake CLI:

* **1 — who may call.**  Every POST needs the per-process bearer token; a POST
  body must be JSON (multipart on the two upload routes); a foreign ``Origin``
  or ``Host`` is refused; the page carries its token inside itself; an
  ``/ask`` attachment must live in the repo, the uploads folder or a project's
  ``design/refs``.
* **4 — the whole tree dies.**  Cancel, timeout, a normal finish and the
  bridge's own death each take down the CLI AND the child it started.
* **5/6 — cost on every terminal state.**  timeout, cancel, error and the
  salvaged no-result turn carry cost, usage and turn count from the stream's
  own ledger; a result that arrived before a kill lands as ``done``.
* **9 — runaway controls.**  ``POST /cancel`` with no id stops what is
  running; ``FORGE_ASSISTANT_MAX_TURN_USD`` stops a turn as ``over_budget``.

Same harness as test_bridge.py; nothing here opens a window or touches the
artist's live bridge (every bridge gets its own port and token file).
"""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
if ASSISTANT_DIR not in sys.path:
    sys.path.insert(0, ASSISTANT_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import bridge  # noqa: E402
from test_bridge import bridge_proc, client, start_bridge  # noqa: E402,F401

#: One fake message: 1000 input + 2000 cache-read + 500 output tokens.  At the
#: Sonnet 4.6 list price (3 / 15 / 3.75 / 0.30 per MTok) that is
#: 3000e-6 + 7500e-6 + 600e-6 = $0.0111; two messages are $0.0222.
SONNET = "claude-sonnet-4-6"
ONE_MESSAGE_USD = 0.0111


def _raw(client, method, path, body=None, headers=None, host=None):
    """``(status, headers, bytes)`` with full control of every header."""
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=30)
    try:
        connection.putrequest(method, path, skip_host=host is not None,
                              skip_accept_encoding=True)
        if host is not None:
            connection.putheader("Host", host)
        data = body if isinstance(body, bytes) else (
            body.encode("utf-8") if body is not None else b"")
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.putheader("Content-Length", str(len(data)))
        connection.endheaders(data)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")}


# ---------------------------------------------------------------------------
# finding 1 — who may call
# ---------------------------------------------------------------------------

def test_the_token_is_written_to_its_file_and_never_published(client):
    assert client.token and len(client.token) >= 32
    for route in ("/health", "/jobs"):
        _status, body = client.request(route)
        assert client.token not in json.dumps(body), route


def test_a_post_without_the_token_is_refused_and_spends_nothing(client):
    status, body = client.request("/ask", {"message": "hi"}, token=False)
    assert status == 401, body
    assert "token" in body["error"] and client.token_path in body["error"]
    status, body = client.request("/ask", {"message": "hi"},
                                  headers={"Authorization": "Bearer nope"},
                                  token=False)
    assert status == 401, body
    status, body = client.request("/new", {}, token=False)
    assert status == 401, body
    time.sleep(0.3)
    assert client.invocations() == []


def test_every_post_route_is_behind_the_token(client):
    """The gate sits before dispatch, so no route can forget it."""
    for route in ("/cancel", "/cancel/abc", "/upload", "/services/start",
                  "/flows", "/flows/run", "/projects/create", "/projects/attach",
                  "/scene/delete", "/models/open", "/projects/x/joint_move",
                  "/projects/x/refs", "/projects/x/build", "/no/such/route"):
        status, body = client.request(route, {}, token=False)
        assert status == 401, (route, status, body)


def test_a_simple_request_body_is_refused_even_with_the_token(client):
    """text/plain is what a cross-origin page can send without a preflight."""
    for kind in ("text/plain", "application/x-www-form-urlencoded",
                 "multipart/form-data; boundary=x"):
        status, _headers, raw = _raw(client, "POST", "/ask",
                                     body='{"message": "status?"}',
                                     headers=dict(client.auth_headers(),
                                                  **{"Content-Type": kind}))
        assert status == 415, (kind, raw)
    # multipart is accepted where an upload is expected (and then judged on
    # its content, which here is empty: a 400, not a 415)
    status, _headers, raw = _raw(client, "POST", "/upload", body="--x--",
                                 headers=dict(client.auth_headers(), **{
                                     "Content-Type": "multipart/form-data; boundary=x"}))
    assert status == 400, raw
    assert client.invocations() == []


def test_an_empty_post_needs_no_content_type(client):
    status, _headers, raw = _raw(client, "POST", "/new",
                                 headers=client.auth_headers())
    assert status == 200, raw


def test_a_foreign_origin_is_refused_and_the_own_origin_is_not(client):
    own = "http://127.0.0.1:%d" % client.port
    status, body = client.request("/ask", {"message": "hi"},
                                  headers={"Origin": "https://evil.example"})
    assert status == 403, body
    assert "evil.example" in body["error"]
    status, body = client.request("/jobs", headers={"Origin": "null"})
    assert status == 403, body
    status, body = client.request("/new", {}, headers={"Origin": own})
    assert status == 200, body
    status, body = client.request(
        "/new", {}, headers={"Origin": "http://localhost:%d" % client.port})
    assert status == 200, body


def test_a_rebinding_host_cannot_read_the_page_or_the_jobs(client):
    """DNS rebinding: the page arrives as Host: evil.example — refused."""
    for route in ("/", "/jobs", "/health"):
        status, _headers, raw = _raw(client, "GET", route,
                                     host="evil.example:%d" % client.port)
        assert status == 403, (route, raw)
        assert client.token.encode("ascii") not in raw
    status, _headers, _raw_body = _raw(client, "GET", "/health",
                                       host="localhost:%d" % client.port)
    assert status == 200


def test_the_page_carries_the_token_and_the_script_sends_it(client):
    status, headers, raw = _raw(client, "GET", "/")
    assert status == 200
    page = raw.decode("utf-8")
    assert ('<meta name="%s" content="%s">' % (bridge.TOKEN_META_NAME, client.token)
            in page)
    assert headers.get("Cache-Control") == "no-store"
    assert headers.get("Referrer-Policy") == "no-referrer"
    # The static copy of the page carries no token at all.
    _status, _headers, static = _raw(client, "GET", "/webui/index.html")
    assert client.token.encode("ascii") not in static
    with open(os.path.join(ASSISTANT_DIR, "webui", "app.js"), encoding="utf-8") as handle:
        script = handle.read()
    api = script[script.index("function api("):]
    api = api[:api.index("\n  }\n")]
    assert '"Authorization"' in api and "BRIDGE_TOKEN" in api
    assert 'meta[name="forge-bridge-token"]' in script


def test_a_refused_post_never_poisons_the_connection(client):
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=30)
    try:
        body = json.dumps({"message": "x" * 5000}).encode("utf-8")
        connection.request("POST", "/ask", body=body,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        # the refusal closed the socket; the next request opens a clean one
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200, response.read()
        response.read()
    finally:
        connection.close()


def test_the_benchmark_runner_reaches_the_real_bridge_with_its_token(client):
    """The runner's client against THIS bridge, not a stub: the two agree."""
    repo = os.path.normpath(os.path.join(ASSISTANT_DIR, os.pardir))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from benchmark import runner

    bench = runner.BridgeClient("http://127.0.0.1:%d" % client.port,
                                token_file=client.token_path)
    status, body = bench.ask("hello from the benchmark")
    assert status == 200, body
    assert client.wait(body["job_id"])["state"] == "done"
    stranger = runner.BridgeClient("http://127.0.0.1:%d" % client.port,
                                   token_file=client.token_path + ".absent")
    status, body = stranger.ask("no token")
    assert status == 401 and "token" in body["error"], body


def test_write_token_file_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "sub" / "token"
    written = bridge.write_token_file(str(target))
    assert written == str(target)
    assert target.read_text(encoding="ascii") == bridge.bridge_token()
    assert not list((tmp_path / "sub").glob("*.tmp"))
    if os.name != "nt":
        assert (os.stat(written).st_mode & 0o077) == 0
    assert bridge.token_matches("Bearer %s" % bridge.bridge_token())
    assert bridge.token_matches("bearer %s" % bridge.bridge_token())
    assert not bridge.token_matches(bridge.bridge_token())
    assert not bridge.token_matches("Bearer ")
    assert not bridge.token_matches(None)


# -- /ask attachments --------------------------------------------------------

def _png(path):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    return str(path)


def test_an_attachment_outside_the_workspace_is_refused_by_name(bridge_proc, tmp_path):
    client = bridge_proc(env_extra={
        "FORGE_ASSISTANT_UPLOADS": str(tmp_path / "uploads"),
        "FORGE_PROJECTS_DIR": str(tmp_path / "projects")})
    outside = _png(tmp_path / "elsewhere" / "secret.png")
    status, body = client.request("/ask", {"message": "look",
                                           "context": {"image_path": outside}})
    assert status == 400, body
    assert outside in body["error"] and "outside" in body["error"]
    assert client.invocations() == []
    # …and no token was minted for it: nothing in /jobs points at it
    _status, jobs = client.request("/jobs")
    assert "secret.png" not in json.dumps(jobs)


def test_attachments_from_uploads_refs_and_the_repo_are_taken(bridge_proc, tmp_path):
    client = bridge_proc(env_extra={
        "FORGE_ASSISTANT_UPLOADS": str(tmp_path / "uploads"),
        "FORGE_PROJECTS_DIR": str(tmp_path / "projects")})
    uploaded = _png(tmp_path / "uploads" / "sketch.png")
    ref = _png(tmp_path / "projects" / "cup" / "design" / "refs" / "front.png")
    # A frozen benchmark reference: exactly what the benchmark runner attaches.
    in_repo = os.path.join(bridge.REPO_ROOT, "benchmark", "tasks", "ear-sculpt",
                           "ear_ref.png")
    paths = [uploaded, ref] + ([in_repo] if os.path.isfile(in_repo) else [])
    for path in paths:
        final = client.turn("look", context={"image_path": path})
        assert final["state"] == "done", (path, final)


def test_image_location_error_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_UPLOADS", str(tmp_path / "up"))
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path / "projects"))
    assert bridge.image_location_error("") == ""
    assert bridge.image_location_error(os.path.join(bridge.REPO_ROOT, "x.png")) == ""
    assert bridge.image_location_error(str(tmp_path / "up" / "a.png")) == ""
    assert bridge.image_location_error(
        str(tmp_path / "projects" / "p" / "design" / "refs" / "a.png")) == ""
    # a refs folder anywhere else is not a project's refs folder
    stray = str(tmp_path / "other" / "p" / "design" / "refs" / "a.png")
    assert "outside" in bridge.image_location_error(stray)
    # traversal out of the uploads folder is judged on the resolved path
    sneaky = str(tmp_path / "up" / ".." / "b.png")
    assert "outside" in bridge.image_location_error(sneaky)


# ---------------------------------------------------------------------------
# finding 4 — the CLI's whole tree dies with the turn and with the bridge
# ---------------------------------------------------------------------------

def _alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _wait_pids(pidfile, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(pidfile):
            with open(pidfile, encoding="utf-8") as handle:
                return json.load(handle)
        time.sleep(0.05)
    raise AssertionError("the fake CLI never started its child")


def _assert_all_dead(pids, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(_alive(pid) for pid in pids.values()):
            return
        time.sleep(0.1)
    raise AssertionError("still running after %.0fs: %s" % (
        timeout, {name: pid for name, pid in pids.items() if _alive(pid)}))


def test_cancel_kills_the_grandchild_too(bridge_proc, tmp_path):
    pidfile = str(tmp_path / "pids.json")
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "slow",
                                    "FAKE_CLAUDE_SLEEP": "120",
                                    "FAKE_CLAUDE_CHILD_PIDFILE": pidfile})
    status, started = client.ask("something slow")
    assert status == 200, started
    pids = _wait_pids(pidfile)
    assert _alive(pids["child"]) and _alive(pids["cli"])
    status, body = client.request("/cancel/%s" % started["job_id"], {})
    assert status == 200, body
    assert client.wait(started["job_id"], timeout=30.0)["state"] == "cancelled"
    _assert_all_dead(pids)


def test_a_timeout_kills_the_grandchild_too(bridge_proc, tmp_path):
    pidfile = str(tmp_path / "pids.json")
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "stream_hang",
                                    "FORGE_ASSISTANT_TIMEOUT": "5",
                                    "FORGE_ASSISTANT_STALL_TIMEOUT": "0",
                                    "FAKE_CLAUDE_CHILD_PIDFILE": pidfile})
    status, started = client.ask("hang")
    assert status == 200
    pids = _wait_pids(pidfile)
    assert client.wait(started["job_id"], timeout=60.0)["state"] == "timeout"
    _assert_all_dead(pids)


def test_a_finished_turn_reaps_what_the_cli_left_running(bridge_proc, tmp_path):
    """The CLI exits cleanly but its child does not: the job's close takes it."""
    pidfile = str(tmp_path / "pids.json")
    client = bridge_proc(env_extra={"FAKE_CLAUDE_CHILD_PIDFILE": pidfile})
    assert client.turn("hello")["state"] == "done"
    _assert_all_dead(_wait_pids(pidfile))


def test_the_bridge_dying_takes_the_cli_tree_with_it(tmp_path):
    pidfile = str(tmp_path / "pids.json")
    proc, client = start_bridge(tmp_path, env_extra={
        "FAKE_CLAUDE_MODE": "slow", "FAKE_CLAUDE_SLEEP": "120",
        "FAKE_CLAUDE_CHILD_PIDFILE": pidfile})
    try:
        status, _started = client.ask("something slow")
        assert status == 200
        pids = _wait_pids(pidfile)
        assert _alive(pids["child"])
        proc.kill()   # no shutdown handler runs: only kill-on-close can save us
        proc.wait(timeout=15)
        _assert_all_dead(pids)
    finally:
        if proc.poll() is None:
            proc.kill()


# ---------------------------------------------------------------------------
# findings 5 and 6 — cost on every terminal state, and results win the race
# ---------------------------------------------------------------------------

def _usage_client(bridge_proc, mode, **env):
    extra = {"FAKE_CLAUDE_MODE": mode, "FAKE_CLAUDE_USAGE": "2",
             "FAKE_CLAUDE_USAGE_MODEL": SONNET}
    extra.update(env)
    return bridge_proc(env_extra=extra)


def _assert_two_message_ledger(final):
    assert final["cost_usd"] == pytest.approx(2 * ONE_MESSAGE_USD), final
    assert final.get("cost_estimated") is True, final
    assert final["num_turns"] == 2, final
    # summed once per message, not once per event that repeated it
    assert final["usage"]["input_tokens"] == 2000, final["usage"]
    assert final["usage"]["cache_read_input_tokens"] == 4000, final["usage"]
    assert final["usage"]["output_tokens"] == 1000, final["usage"]
    assert final["model"] == "default-model", final   # the init event's model
    assert final["session_id"] == "sess-fake-0001", final


def test_a_timeout_is_billed_from_the_streamed_usage(bridge_proc):
    client = _usage_client(bridge_proc, "stream_hang",
                           FORGE_ASSISTANT_TIMEOUT="5",
                           FORGE_ASSISTANT_STALL_TIMEOUT="0")
    final = client.turn("build it")
    assert final["state"] == "timeout", final
    _assert_two_message_ledger(final)
    assert final["session_cost_usd"] == pytest.approx(2 * ONE_MESSAGE_USD)


def test_a_cancel_is_billed_and_keeps_what_it_did(bridge_proc):
    client = _usage_client(bridge_proc, "stream_slow", FAKE_CLAUDE_SLEEP="60")
    status, started = client.ask("cut this up")
    assert status == 200
    deadline = time.time() + 30.0
    while time.time() < deadline:
        _status, body = client.request("/job/%s" % started["job_id"])
        if len(body.get("activity") or []) >= 2:
            break
        time.sleep(0.1)
    time.sleep(0.3)
    client.request("/cancel/%s" % started["job_id"], {})
    final = client.wait(started["job_id"], timeout=30.0)
    assert final["state"] == "cancelled", final
    _assert_two_message_ledger(final)
    assert "stopped at your request" in final["reply"], final["reply"]
    assert "partforge_check" in final["reply"], final["reply"]
    _status, health = client.request("/health")
    assert health["session_cost_usd"] == pytest.approx(2 * ONE_MESSAGE_USD)
    assert health["busy"] is False


def test_a_salvaged_turn_with_no_result_is_billed_from_the_ledger(bridge_proc):
    client = _usage_client(bridge_proc, "stream_noresult")
    final = client.turn("segment this")
    assert final["state"] == "done", final
    _assert_two_message_ledger(final)


def test_a_turn_with_a_result_keeps_the_clis_own_figure(bridge_proc):
    client = _usage_client(bridge_proc, "stream")
    final = client.turn("segment this")
    assert final["state"] == "done", final
    assert final["cost_usd"] == pytest.approx(0.0123), final
    assert "cost_estimated" not in final, final
    assert final["num_turns"] == 2   # the result's own count


def test_a_result_that_arrived_before_the_timeout_lands_as_done(bridge_proc):
    """Finding 6: the CLI lingers after its result; the watchdog must not win."""
    client = bridge_proc(env_extra={"FAKE_CLAUDE_LINGER": "60",
                                    "FORGE_ASSISTANT_TIMEOUT": "5",
                                    "FORGE_ASSISTANT_STALL_TIMEOUT": "0"})
    final = client.turn("hello")
    assert final["state"] == "done", final
    assert "OK" in final["reply"]
    assert final["cost_usd"] == pytest.approx(0.0123)
    assert final["num_turns"] == 1
    assert final["duration_ms"] < 30000, final["duration_ms"]


def test_a_cancel_after_the_result_does_not_undo_the_answer(bridge_proc, tmp_path):
    flag = str(tmp_path / "result-sent.flag")
    client = bridge_proc(env_extra={"FAKE_CLAUDE_LINGER": "60",
                                    "FAKE_CLAUDE_LINGER_FLAG": flag})
    status, started = client.ask("hello")
    assert status == 200
    deadline = time.time() + 30.0
    while not os.path.isfile(flag) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)   # the bridge reads the line; the process lingers on
    client.request("/cancel/%s" % started["job_id"], {})
    final = client.wait(started["job_id"], timeout=30.0)
    assert final["state"] == "done", final
    assert final["cost_usd"] == pytest.approx(0.0123)
    _status, health = client.request("/health")
    assert health["session_cost_usd"] == pytest.approx(0.0123)


def test_the_ledger_counts_each_message_once():
    store = bridge.JobStore()
    job, _disposition = store.submit("hi", "hi")
    recorder = bridge.ActivityRecorder(store, job["job_id"], interval=0)
    assert recorder.totals()["cost_usd"] is None
    recorder.feed({"type": "system", "subtype": "init", "session_id": "s-1",
                   "model": "claude-haiku-4-5"})
    for _repeat in range(3):   # message_start, delta and assistant, thrice over
        recorder.feed({"type": "stream_event", "event": {
            "type": "message_start", "message": {
                "id": "m1", "model": "claude-haiku-4-5",
                "usage": {"input_tokens": 1000000, "output_tokens": 1}}}})
        recorder.feed({"type": "stream_event", "event": {
            "type": "message_delta", "usage": {"output_tokens": 100000}}})
        recorder.feed({"type": "assistant", "message": {
            "id": "m1", "content": [],
            "usage": {"input_tokens": 1000000, "output_tokens": 100000}}})
    totals = recorder.totals()
    # haiku 4.5: $1 per M input + $5 per M output -> 1.0 + 0.5
    assert totals["cost_usd"] == pytest.approx(1.5)
    assert totals["cost_estimated"] is True
    assert totals["num_turns"] == 1
    assert totals["usage"] == {"input_tokens": 1000000, "output_tokens": 100000}
    assert totals["model"] == "claude-haiku-4-5"
    assert recorder.session_id() == "s-1"
    # a figure the stream volunteers beats the estimate
    recorder.feed({"type": "stream_event", "event": {
        "type": "message_delta", "total_cost_usd": 0.9}})
    assert recorder.spent() == (0.9, False)


def test_model_prices_pick_the_most_specific_row():
    assert bridge.model_price("claude-opus-4-8") == (5.0, 25.0, 6.25, 0.5)
    assert bridge.model_price("claude-opus-4-1-20250805")[0] == 15.0
    assert bridge.model_price("claude-opus-5-5")[0] == 4.0
    assert bridge.model_price("claude-sonnet-5")[0] == 2.0
    assert bridge.model_price("claude-sonnet-4-6")[0] == 3.0
    assert bridge.model_price("haiku")[0] == 1.0
    assert bridge.model_price("something-new") == bridge.DEFAULT_MODEL_PRICE


# ---------------------------------------------------------------------------
# finding 9 — runaway controls
# ---------------------------------------------------------------------------

def test_cancel_with_no_id_stops_whatever_is_running(bridge_proc):
    client = bridge_proc(env_extra={"FAKE_CLAUDE_MODE": "slow",
                                    "FAKE_CLAUDE_SLEEP": "60"})
    status, body = client.request("/cancel", {})
    assert status == 404 and "Nothing is running" in body["error"], body

    status, started = client.ask("something slow")
    assert status == 200
    client.wait_for_calls(1)
    status, body = client.request("/cancel", {})
    assert status == 200, body
    assert body["job_id"] == started["job_id"]
    assert client.wait(started["job_id"], timeout=30.0)["state"] == "cancelled"
    # the trailing-slash spelling is the same route
    status, body = client.request("/cancel/", {})
    assert status == 404, body


def test_the_spend_cap_stops_a_runaway_as_over_budget(bridge_proc, tmp_path):
    pidfile = str(tmp_path / "pids.json")
    client = _usage_client(bridge_proc, "stream_hang",
                           FORGE_ASSISTANT_MAX_TURN_USD="0.015",
                           FORGE_ASSISTANT_STALL_TIMEOUT="0",
                           FAKE_CLAUDE_CHILD_PIDFILE=pidfile)
    started = time.time()
    final = client.turn("build everything")
    assert time.time() - started < 45.0   # not the 600 s budget, not the hang
    assert final["state"] == bridge.OVER_BUDGET_STATE == "over_budget", final
    _assert_two_message_ledger(final)     # message 2 is what crossed $0.015
    assert final["spend_cap_usd"] == pytest.approx(0.015)
    assert "spend cap" in final["reply"] and "$0.02" in final["reply"], final["reply"]
    _assert_all_dead(_wait_pids(pidfile))
    _status, health = client.request("/health")
    assert health["busy"] is False
    assert health["session_cost_usd"] == pytest.approx(2 * ONE_MESSAGE_USD)


def test_a_turn_under_the_cap_is_untouched(bridge_proc):
    client = _usage_client(bridge_proc, "stream",
                           FORGE_ASSISTANT_MAX_TURN_USD="1.00")
    assert client.turn("segment this")["state"] == "done"


def test_the_spend_cap_env_is_off_unless_it_is_a_positive_number(monkeypatch):
    for value in ("", "0", "-1", "lots"):
        monkeypatch.setenv("FORGE_ASSISTANT_MAX_TURN_USD", value)
        assert bridge.max_turn_usd() is None, value
    monkeypatch.setenv("FORGE_ASSISTANT_MAX_TURN_USD", " 2.5 ")
    assert bridge.max_turn_usd() == 2.5
    monkeypatch.delenv("FORGE_ASSISTANT_MAX_TURN_USD")
    assert bridge.max_turn_usd() is None
