"""A very small RFC 6455 client - only what ComfyUI's /ws progress feed needs.

The whole service is stdlib-only, and ComfyUI reports real per-step progress
over its websocket and nowhere else (the HTTP job API gives state, not a
fraction).  Rather than take a dependency for one read-only text stream, this
does the handshake and unmasks incoming text frames by hand.

Scope on purpose: client-to-server text only for the handshake, no extensions,
no continuation-heavy edge cases beyond simple fragmentation, no TLS.  It talks
to 127.0.0.1 and nothing else.  Any failure is non-fatal to a job - the caller
falls back to state-only progress.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(OSError):
    pass


class WebSocket:
    """Blocking websocket reader.  Use as a context manager."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.sock = None
        self._buf = b""

    # -- lifecycle -------------------------------------------------------
    def connect(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise WebSocketError("server closed during handshake")
            header += chunk
        head, _, rest = header.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status_line:
            sock.close()
            raise WebSocketError(f"handshake refused: {status_line}")

        self.sock = sock
        self._buf = rest
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- reading ---------------------------------------------------------
    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _frame(self):
        b0, b1 = self._read(2)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read(8))
        mask = self._read(4) if masked else None
        payload = self._read(length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self):
        """Return the next text message as a str, or None for binary/close.

        Raises :class:`WebSocketError` when the peer goes away and
        ``socket.timeout`` when nothing arrives within ``timeout``.
        """
        data = b""
        opcode = None
        while True:
            fin, op, payload = self._frame()
            if op == _OP_PING:
                self._send_pong(payload)
                continue
            if op == _OP_PONG:
                continue
            if op == _OP_CLOSE:
                raise WebSocketError("peer closed the socket")
            if op != _OP_CONT:
                opcode = op
            data += payload
            if fin:
                break
        if opcode == _OP_TEXT:
            return data.decode("utf-8", "replace")
        return None

    def recv_json(self):
        text = self.recv_text()
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _send_pong(self, payload: bytes):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytes([0x80 | _OP_PONG])
        n = len(masked)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(header + mask + masked)
