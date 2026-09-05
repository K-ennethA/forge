"""NDJSON framing against a fake Blender add-on socket.

The fake server is a plain stdlib socket on an OS-assigned ephemeral port — never
9876 — so these run with Blender closed and cannot collide with a real add-on.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Callable

import pytest

from forge_mcp import blender_client, config
from forge_mcp.errors import BackendError, BackendUnavailable

from .conftest import REAL_BACKEND_PORTS, free_port

Responder = Callable[[dict[str, Any], socket.socket], None]


class FakeBlender:
    """Accepts a connection, reads one NDJSON line, hands it to `responder`.

    One connection by default, because the client opens a fresh one per command.
    `connections=N` serves N commands in a row, for the tools that make more than
    one call (rigforge_status asks for the scene, then for the tags).
    """

    def __init__(self, responder: Responder, connections: int = 1) -> None:
        self.responder = responder
        self.connections = connections
        self.requests: list[dict[str, Any]] = []
        self.raw_requests: list[bytes] = []
        self.error: BaseException | None = None
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = int(self._sock.getsockname()[1])
        assert self.port not in REAL_BACKEND_PORTS
        self._sock.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeBlender":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        # Signal first: a blocking accept() is not reliably interrupted by
        # close() on Windows, so the loop polls the event instead.
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        served = 0
        while not self._stop.is_set() and served < self.connections:
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                try:
                    buffer = b""
                    while b"\n" not in buffer:
                        chunk = conn.recv(65536)
                        if not chunk:
                            return
                        buffer += chunk
                    line, _, _ = buffer.partition(b"\n")
                    self.raw_requests.append(line)
                    self.requests.append(json.loads(line.decode("utf-8")))
                    self.responder(self.requests[-1], conn)
                except BaseException as exc:  # noqa: BLE001 - surfaced to the test
                    self.error = exc
            served += 1  # one command per connection (one connection per call)


def reply(payload: dict[str, Any]) -> Responder:
    def respond(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")

    return respond


@pytest.fixture
def point_at(monkeypatch):
    """Aim blender_client at a fake server's port."""

    def apply(port: int) -> None:
        monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "BLENDER_PORT", port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 5.0)

    return apply


# --- request framing --------------------------------------------------------


def test_request_is_one_json_line_in_contract_shape(point_at) -> None:
    with FakeBlender(reply({"status": "success", "result": {}})) as server:
        point_at(server.port)
        blender_client.send_command("symmetrize", {"object": "Cube", "direction": "+X"})

    assert server.error is None
    (raw,) = server.raw_requests
    assert b"\n" not in raw
    request = server.requests[0]
    assert request["type"] == "symmetrize"
    assert request["params"] == {"object": "Cube", "direction": "+X"}
    assert isinstance(request["id"], str) and request["id"]


def test_omitted_params_become_an_empty_object(point_at) -> None:
    with FakeBlender(reply({"status": "success", "result": {}})) as server:
        point_at(server.port)
        blender_client.send_command("ping")
    assert server.requests[0]["params"] == {}


def test_unencodable_params_raise_before_connecting(dead_backends) -> None:
    """NaN is not JSON; that must fail as an encode error, not "Blender is down"."""
    with pytest.raises(BackendError, match="Could not encode parameters"):
        blender_client.send_command("load_mesh", {"vertices": float("nan")})


# --- response framing -------------------------------------------------------


def test_success_returns_the_result_object(point_at) -> None:
    payload = {"status": "success", "result": {"vertex_count": 8, "face_count": 6}}
    with FakeBlender(reply(payload)) as server:
        point_at(server.port)
        result = blender_client.send_command("remesh", {"mode": "voxel"})
    assert result == {"vertex_count": 8, "face_count": 6}


def test_null_result_becomes_empty_dict(point_at) -> None:
    with FakeBlender(reply({"status": "success", "result": None})) as server:
        point_at(server.port)
        assert blender_client.send_command("shade") == {}


def test_response_split_across_several_writes_is_reassembled(point_at) -> None:
    """The reply arrives in pieces; only the newline ends the frame."""
    body = json.dumps(
        {"status": "success", "result": {"objects": [f"Piece.{i:03d}" for i in range(400)]}}
    ).encode("utf-8")

    def dribble(_request: dict[str, Any], conn: socket.socket) -> None:
        for start in range(0, len(body), 100):
            conn.sendall(body[start : start + 100])
            time.sleep(0.002)
        conn.sendall(b"\n")

    with FakeBlender(dribble) as server:
        point_at(server.port)
        result = blender_client.send_command("separate_loose")

    assert server.error is None
    assert len(result["objects"]) == 400
    assert result["objects"][0] == "Piece.000"


def test_trailing_bytes_after_the_newline_are_ignored(point_at) -> None:
    def with_trailer(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.sendall(b'{"status":"success","result":{"ok":1}}\n{"junk":true}\n')

    with FakeBlender(with_trailer) as server:
        point_at(server.port)
        assert blender_client.send_command("ping") == {"ok": 1}


def test_non_ascii_round_trips_as_utf8(point_at) -> None:
    payload = {"status": "success", "result": {"name": "Bücher–Ständer"}}
    with FakeBlender(reply(payload)) as server:
        point_at(server.port)
        assert blender_client.send_command("rename_object")["name"] == "Bücher–Ständer"


# --- error paths ------------------------------------------------------------


def test_error_status_becomes_backend_error_with_the_addons_message(point_at) -> None:
    payload = {"status": "error", "result": None, "message": "Object 'Cube' not found"}
    with FakeBlender(reply(payload)) as server:
        point_at(server.port)
        with pytest.raises(BackendError) as exc:
            blender_client.send_command("select_object", {"name": "Cube"})
    assert "Blender rejected 'select_object'" in str(exc.value)
    assert "Object 'Cube' not found" in str(exc.value)


def test_error_status_without_a_message_still_reports(point_at) -> None:
    with FakeBlender(reply({"status": "error"})) as server:
        point_at(server.port)
        with pytest.raises(BackendError, match="no message given"):
            blender_client.send_command("remesh")


def test_non_json_response_is_reported_with_a_preview(point_at) -> None:
    def garbage(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.sendall(b"<html>not json</html>\n")

    with FakeBlender(garbage) as server:
        point_at(server.port)
        with pytest.raises(BackendError) as exc:
            blender_client.send_command("ping")
    assert "non-JSON response" in str(exc.value)
    assert "not json" in str(exc.value)


def test_non_object_response_is_rejected(point_at) -> None:
    def a_list(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.sendall(b"[1, 2, 3]\n")

    with FakeBlender(a_list) as server:
        point_at(server.port)
        with pytest.raises(BackendError, match="non-object response"):
            blender_client.send_command("ping")


def test_connection_closed_before_any_reply_is_backend_unavailable(point_at) -> None:
    def hang_up(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.close()

    with FakeBlender(hang_up) as server:
        point_at(server.port)
        with pytest.raises(BackendUnavailable) as exc:
            blender_client.send_command("ping")
    assert "Blender is not running" in str(exc.value)


def test_partial_line_then_close_is_a_backend_error(point_at) -> None:
    """Bytes but no newline: the add-on died mid-response, not "never started"."""

    def truncate(_request: dict[str, Any], conn: socket.socket) -> None:
        conn.sendall(b'{"status":"success","resu')
        conn.close()

    with FakeBlender(truncate) as server:
        point_at(server.port)
        with pytest.raises(BackendError) as exc:
            blender_client.send_command("ping")
    assert "before sending a complete" in str(exc.value)


def test_read_timeout_message_points_at_the_env_override(point_at, monkeypatch) -> None:
    def never_answer(_request: dict[str, Any], conn: socket.socket) -> None:
        time.sleep(2.0)

    with FakeBlender(never_answer) as server:
        point_at(server.port)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 0.3)
        with pytest.raises(BackendError) as exc:
            blender_client.send_command("remesh")
    assert "did not answer within" in str(exc.value)
    assert "FORGE_BLENDER_READ_TIMEOUT" in str(exc.value)


def test_oversized_unterminated_response_is_aborted(point_at, monkeypatch) -> None:
    def flood(_request: dict[str, Any], conn: socket.socket) -> None:
        blob = b"x" * 8192
        try:
            for _ in range(200):
                conn.sendall(blob)
        except OSError:
            pass

    with FakeBlender(flood) as server:
        point_at(server.port)
        monkeypatch.setattr(config, "MAX_RESPONSE_BYTES", 32 * 1024)
        with pytest.raises(BackendError, match="without a line terminator"):
            blender_client.send_command("get_scene_info")


def test_nothing_listening_is_backend_unavailable(dead_backends) -> None:
    with pytest.raises(BackendUnavailable) as exc:
        blender_client.send_command("ping")
    message = str(exc.value)
    assert "Blender is not running or the Forge add-on server is stopped" in message
    assert "Start Server" in message
    assert f"127.0.0.1:{dead_backends[0]}" in message


def test_is_available_reflects_the_listener(point_at, dead_backends) -> None:
    assert blender_client.is_available() is False
    with FakeBlender(reply({"status": "success", "result": {}})) as server:
        point_at(server.port)
        assert blender_client.is_available() is True


def test_free_port_never_returns_a_real_backend_port() -> None:
    assert free_port() not in REAL_BACKEND_PORTS
