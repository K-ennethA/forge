"""Newline-delimited JSON client for the Forge Blender add-on socket.

Wire format (docs/architecture.md):

    request  {"id": "<optional>", "type": "<command>", "params": {...}}\\n
    response {"id": "<echoed>", "status": "success"|"error",
              "result": <object|null>, "message": "<set on error>"}\\n

One connection per command. The add-on's socket server is single-threaded from
the caller's point of view (bpy work is marshalled onto the main thread through
a timer queue), so pooling buys nothing and a fresh connection means a stalled
Blender can never poison later calls.
"""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any, Dict, Optional

from . import config
from .errors import BLENDER_DOWN, BackendError, BackendUnavailable

_RECV_CHUNK = 65536


def _read_line(sock: socket.socket) -> bytes:
    """Read bytes up to (not including) the first newline."""
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = sock.recv(_RECV_CHUNK)
        except TimeoutError as exc:  # socket.timeout is an alias in 3.10+
            raise BackendError(
                "Blender did not answer within "
                f"{config.BLENDER_READ_TIMEOUT:.0f}s. The operation may still be "
                "running on Blender's main thread — check the Blender window, "
                "and raise FORGE_BLENDER_READ_TIMEOUT for very heavy meshes."
            ) from exc
        except OSError as exc:
            raise BackendUnavailable(
                BLENDER_DOWN.format(address=config.blender_address())
                + f" (connection dropped mid-response: {exc})"
            ) from exc

        if not chunk:
            if chunks:
                raise BackendError(
                    "Blender closed the connection before sending a complete "
                    "response line. The add-on may have hit an unhandled error "
                    "— check Blender's system console."
                )
            raise BackendUnavailable(
                BLENDER_DOWN.format(address=config.blender_address())
                + " (connection closed with no response)"
            )

        newline = chunk.find(b"\n")
        if newline != -1:
            chunks.append(chunk[:newline])
            return b"".join(chunks)

        chunks.append(chunk)
        total += len(chunk)
        if total > config.MAX_RESPONSE_BYTES:
            raise BackendError(
                "Blender's response exceeded "
                f"{config.MAX_RESPONSE_BYTES} bytes without a line terminator; "
                "aborting to avoid exhausting memory."
            )


def send_command(
    command_type: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    read_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Send one command and return its ``result`` object.

    Raises BackendUnavailable if Blender/the add-on server is not listening, and
    BackendError if the add-on answered with ``status: "error"`` or with
    something that is not a well-formed response.
    """
    request: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "type": command_type,
        "params": params or {},
    }
    try:
        payload = json.dumps(request, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BackendError(
            f"Could not encode parameters for '{command_type}' as JSON: {exc}"
        ) from exc

    data = payload.encode("utf-8") + b"\n"

    try:
        sock = socket.create_connection(
            (config.BLENDER_HOST, config.BLENDER_PORT),
            timeout=config.BLENDER_CONNECT_TIMEOUT,
        )
    except OSError as exc:
        raise BackendUnavailable(
            BLENDER_DOWN.format(address=config.blender_address())
        ) from exc

    try:
        sock.settimeout(
            read_timeout if read_timeout is not None else config.BLENDER_READ_TIMEOUT
        )
        try:
            sock.sendall(data)
        except OSError as exc:
            raise BackendUnavailable(
                BLENDER_DOWN.format(address=config.blender_address())
                + f" (send failed: {exc})"
            ) from exc
        line = _read_line(sock)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    try:
        response = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError as exc:
        preview = line[:400].decode("utf-8", errors="replace")
        raise BackendError(
            f"Blender sent a non-JSON response to '{command_type}': {preview!r}"
        ) from exc

    if not isinstance(response, dict):
        raise BackendError(
            f"Blender sent a non-object response to '{command_type}': {response!r}"
        )

    if response.get("status") == "error":
        message = response.get("message") or "no message given"
        raise BackendError(f"Blender rejected '{command_type}': {message}")

    result = response.get("result")
    return result if isinstance(result, dict) else {}


def is_available() -> bool:
    """True if something accepts a TCP connection on the add-on's port."""
    try:
        with socket.create_connection(
            (config.BLENDER_HOST, config.BLENDER_PORT),
            timeout=config.BLENDER_CONNECT_TIMEOUT,
        ):
            return True
    except OSError:
        return False
