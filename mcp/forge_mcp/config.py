"""Connection settings for both Forge backends.

Defaults match docs/architecture.md. Every value can be overridden with an
environment variable, which is how .mcp.json would point the server at a
non-default port without editing code.
"""

from __future__ import annotations

import os


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


# --- Blender add-on socket (docs/architecture.md: TCP 127.0.0.1:9876) --------
BLENDER_HOST: str = _env_str("FORGE_BLENDER_HOST", "127.0.0.1")
BLENDER_PORT: int = _env_int("FORGE_BLENDER_PORT", 9876)

# Short, so "is Blender up?" fails fast instead of hanging a tool call.
BLENDER_CONNECT_TIMEOUT: float = _env_float("FORGE_BLENDER_CONNECT_TIMEOUT", 2.0)
# Generous, because remesh/quadriflow on a dense mesh blocks Blender's main thread.
BLENDER_READ_TIMEOUT: float = _env_float("FORGE_BLENDER_READ_TIMEOUT", 180.0)

# --- Geometry service HTTP (docs/architecture.md: 127.0.0.1:8765) -----------
SERVICE_HOST: str = _env_str("FORGE_SERVICE_HOST", "127.0.0.1")
SERVICE_PORT: int = _env_int("FORGE_SERVICE_PORT", 8765)
SERVICE_URL: str = _env_str(
    "FORGE_SERVICE_URL", f"http://{SERVICE_HOST}:{SERVICE_PORT}"
).rstrip("/")

SERVICE_CONNECT_TIMEOUT: float = _env_float("FORGE_SERVICE_CONNECT_TIMEOUT", 2.0)
SERVICE_READ_TIMEOUT: float = _env_float("FORGE_SERVICE_READ_TIMEOUT", 180.0)

# Refuse to buffer a runaway response rather than eating all of RAM.
MAX_RESPONSE_BYTES: int = _env_int("FORGE_MAX_RESPONSE_BYTES", 256 * 1024 * 1024)


def blender_address() -> str:
    return f"{BLENDER_HOST}:{BLENDER_PORT}"


def service_address() -> str:
    return SERVICE_URL
