"""Shared fixtures.

Two rules hold for the whole suite:

* Nothing binds or connects to the real backend ports (Blender 9876, geometry
  service 8765). Fake servers take an ephemeral port from the OS, and the
  "backend is down" tests are pointed at a port nothing is listening on, so they
  behave the same whether or not Blender happens to be running on this machine.
* No Blender, no geometry service, no windows. Everything here is stdlib
  sockets, subprocesses and the mcp SDK's own in-memory client.
"""

from __future__ import annotations

import socket

import pytest

from forge_mcp import config

#: Ports owned by the real backends (docs/architecture.md). Never touched here.
REAL_BACKEND_PORTS = {9876, 8765}


def free_port() -> int:
    """An ephemeral port that nothing is listening on, and never a real one."""
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in REAL_BACKEND_PORTS:
            return port
    raise RuntimeError("could not obtain a usable ephemeral port")


@pytest.fixture
def dead_backends(monkeypatch) -> tuple[int, int]:
    """Point both clients at closed ports, with short timeouts.

    Returns (blender_port, service_port).
    """
    blender_port = free_port()
    service_port = free_port()
    assert not REAL_BACKEND_PORTS & {blender_port, service_port}

    monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "BLENDER_PORT", blender_port)
    monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 2.0)

    monkeypatch.setattr(config, "SERVICE_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SERVICE_PORT", service_port)
    monkeypatch.setattr(config, "SERVICE_URL", f"http://127.0.0.1:{service_port}")
    monkeypatch.setattr(config, "SERVICE_CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(config, "SERVICE_READ_TIMEOUT", 2.0)

    return blender_port, service_port
